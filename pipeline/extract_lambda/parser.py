"""
Parses OFAC SLS /entities XML into the row shapes defined in
DATA_DICTIONARY.md. Deliberately has no network or boto3 code in
this file, so it can be unit tested against a saved fixture
without hitting the live API or AWS.

Assumption flagged, not confirmed: the API documentation excerpt
we have shows a single <entity> fragment, not the enclosing root
element or namespace for a multi-entity /entities response.
Namespaces (whatever they turn out to be, if any) are stripped
per-entity in _strip_namespaces() so the rest of this module works
regardless. Entities are discovered via streaming iterparse (see
_iter_entity_elements), not a full ET.fromstring() tree, since the
live SDN list turned out to be large enough that holding the whole
document in memory at once caused real problems on the first live
run; see that function's docstring for why.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from xml.etree import ElementTree as ET

VALID_ENTITY_TYPES = {"Individual", "Entity", "Vessel", "Aircraft"}


def _strip_namespaces(root: ET.Element) -> ET.Element:
    """The API documentation we have doesn't show whether /entities
    responses use an XML namespace at all. Rather than guess one and
    have every find()/findall() call below silently fail against a
    real response that uses a different (or no) namespace, strip
    namespaces from every tag up front so the rest of this module
    can use plain, unqualified tag names regardless."""
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


@dataclass
class ParsedAlias:
    alias_name: str
    alias_type: str | None
    is_low_quality: bool


ADDRESS_PART_TYPES = {
    "CITY": "city",
    "ADDRESS1": "address1",
    "ADDRESS2": "address2",
    "ADDRESS3": "address3",
    "POSTAL CODE": "postal_code",
    "REGION": "region",
}


@dataclass
class ParsedAddress:
    country: str | None
    address1: str | None = None
    address2: str | None = None
    address3: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None


@dataclass
class ParsedEntity:
    ent_num: int
    identity_id: int | None
    entity_type: str
    primary_name: str
    sanctions_programs: list[str]
    date_of_birth: date | None
    gender: str | None
    remarks: str | None
    aliases: list[ParsedAlias] = field(default_factory=list)
    addresses: list[ParsedAddress] = field(default_factory=list)


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _primary_translation(el: ET.Element) -> ET.Element | None:
    """Per Data Dictionary Key Decisions: only the primary (Latin-script)
    translation is captured; non-Latin transliterations are out of scope.
    Works for both <name> and <address> elements, both use the same
    translations/translation shape, confirmed against a real live record."""
    for t in el.findall("translations/translation"):
        if _text(t.find("isPrimary")) == "true":
            return t
    return el.find("translations/translation")


def _parse_address_parts(address_el: ET.Element) -> dict[str, str | None]:
    """Extracts city/address1-3/region/postal_code from the primary
    translation's addressParts. Confirmed against a real production
    file that these are commonly populated (CITY and ADDRESS1 on well
    over half of all address records across ~19,400 entities), not
    edge cases, hence promoting them to real columns rather than
    leaving them uncaptured as the original Data Dictionary draft did
    before a live file was available to check against."""
    fields: dict[str, str | None] = {key: None for key in ADDRESS_PART_TYPES.values()}
    translation = _primary_translation(address_el)
    if translation is None:
        return fields
    for part in translation.findall("addressParts/addressPart"):
        part_type = _text(part.find("type"))
        key = ADDRESS_PART_TYPES.get(part_type) if part_type else None
        if key:
            fields[key] = _text(part.find("value"))
    return fields


def _parse_feature_date(entity_el: ET.Element, feature_type: str) -> str | None:
    """Returns the fromDateBegin of the isPrimary=true feature of the given
    type, or None if absent. Handles the documented case of more than one
    feature of the same type (e.g. an exact DOB and an approximate one)."""
    candidates = [
        f for f in entity_el.findall("features/feature")
        if _text(f.find("type")) == feature_type
    ]
    if not candidates:
        return None
    primary = next((f for f in candidates if _text(f.find("isPrimary")) == "true"), candidates[0])
    return _text(primary.find("valueDate/fromDateBegin"))


def _parse_feature_value(entity_el: ET.Element, feature_type: str) -> str | None:
    candidates = [
        f for f in entity_el.findall("features/feature")
        if _text(f.find("type")) == feature_type
    ]
    if not candidates:
        return None
    primary = next((f for f in candidates if _text(f.find("isPrimary")) == "true"), candidates[0])
    return _text(primary.find("value"))


def parse_entity(entity_el: ET.Element) -> ParsedEntity:
    """Parses a single <entity> element into the sdn_entities /
    sdn_aliases / sdn_addresses row shapes."""
    ent_num = int(entity_el.get("id"))

    identity_id_text = _text(entity_el.find("generalInfo/identityId"))
    identity_id = int(identity_id_text) if identity_id_text else None

    entity_type = _text(entity_el.find("generalInfo/entityType")) or ""
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(
            f"ent_num {ent_num}: unrecognized entityType {entity_type!r}; "
            f"expected one of {sorted(VALID_ENTITY_TYPES)}"
        )

    programs = [
        _text(p) for p in entity_el.findall("sanctionsPrograms/sanctionsProgram")
    ]
    programs = [p for p in programs if p]

    primary_name = None
    aliases: list[ParsedAlias] = []
    for name_el in entity_el.findall("names/name"):
        translation = _primary_translation(name_el)
        full_name = _text(translation.find("formattedFullName")) if translation is not None else None
        if not full_name:
            continue
        if _text(name_el.find("isPrimary")) == "true":
            primary_name = full_name
        else:
            aliases.append(
                ParsedAlias(
                    alias_name=full_name,
                    alias_type=_text(name_el.find("aliasType")),
                    is_low_quality=_text(name_el.find("isLowQuality")) == "true",
                )
            )

    if not primary_name:
        raise ValueError(f"ent_num {ent_num}: no primary name found")

    addresses = [
        ParsedAddress(country=_text(a.find("country")), **_parse_address_parts(a))
        for a in entity_el.findall("addresses/address")
    ]

    dob_text = _parse_feature_date(entity_el, "Birthdate")
    date_of_birth = date.fromisoformat(dob_text) if dob_text else None
    gender = _parse_feature_value(entity_el, "Gender")

    # 'remarks' is not confirmed to exist on the REST response; see
    # Data Dictionary Open Items. Left as None until verified live.
    remarks = _text(entity_el.find("remarks"))

    return ParsedEntity(
        ent_num=ent_num,
        identity_id=identity_id,
        entity_type=entity_type,
        primary_name=primary_name,
        sanctions_programs=programs,
        date_of_birth=date_of_birth,
        gender=gender,
        remarks=remarks,
        aliases=aliases,
        addresses=addresses,
    )


def _iter_entity_elements(xml_bytes: bytes):
    """Yields each <entity> element one at a time via iterparse, clearing
    it from memory once yielded rather than holding the whole document
    as one tree via ET.fromstring(). This is the real fix for what the
    first live run against this API actually showed: the full SDN list
    is large enough that building one complete in-memory tree of it
    exhausted memory, and even with more memory allocated, made parsing
    itself the bottleneck (everything held at once rather than freed as
    it's consumed). clear() removes an <entity>'s own children once
    we're done with it, which is where the bulk of memory was going
    (each entity's nested names/aliases/addresses/features), even though
    the root element still holds a reference to each now-empty entity
    stub, stdlib ElementTree has no parent-pointer removal the way lxml
    does, so this is the standard, good-enough streaming pattern without
    a third-party dependency."""
    saw_any_element = False
    for _event, elem in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
        saw_any_element = True
        local_tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if local_tag == "entity":
            yield _strip_namespaces(elem)
            elem.clear()
    if not saw_any_element:
        raise ValueError("Response contained no XML elements at all")


def parse_entities_xml(xml_bytes: bytes) -> list[ParsedEntity]:
    """Parses a full /entities response into a list of ParsedEntity.
    Fully materializes every entity into structured objects, holding
    them all in memory at once, sized for the Load/Match Lambda, not
    the Extract Lambda. Use count_entities() there instead."""
    return [parse_entity(e) for e in _iter_entity_elements(xml_bytes)]


def count_entities(xml_bytes: bytes) -> int:
    """Streaming count: never holds a full DOM tree or parsed entity
    objects in memory, just increments per <entity> end event, and each
    element is discarded (via _iter_entity_elements's elem.clear())
    immediately after being counted."""
    count = 0
    for _entity_el in _iter_entity_elements(xml_bytes):
        count += 1
    return count
