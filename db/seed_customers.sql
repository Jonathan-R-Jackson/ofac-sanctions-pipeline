-- ============================================================
-- Seeds the customers table with real test data per the Test
-- Plan: true positives (exact and lightly misspelled real SDN
-- names), near-miss cases (real surnames paired with unrelated
-- first names), and true negatives (pure Faker names). All
-- derived from the actual live SDN pull, not invented, per the
-- Test Plan's own reasoning against hardcoding names sight
-- unseen.
--
-- Verified against the real matcher and the full live candidate
-- pool before being handed over: all 10 true positives (exact
-- + misspelled) score above DEFAULT_THRESHOLD (88.0), all 10
-- near-misses and all 10 true negatives score below it,
-- including the real false-positive-shaped case ("Donna
-- Thomas" against the real alias "DON TOMAS") that's what
-- moved the threshold from 85.0 to 88.0 in the first place.
-- ============================================================

BEGIN;

INSERT INTO customers (full_name, is_seeded_positive) VALUES ('RIVERA MARADIAGA, Maira Lizeth', true);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('FARAHANI, Alireza Shahvaroghi', true);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('ZHANG, Wei', true);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('DELCO, Fabio Libero', true);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('KIM, Song', true);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('SUSHKO, Andriy Volbdymyrovych', true);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('FATAHINOJOKAMBARG, Alireza', true);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('ZCKHAROV, Nikita Aleksandrovich', true);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('MHLLER, Siraaj', true);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('KHRSHGHADAM, Mehdi', true);

INSERT INTO customers (full_name, is_seeded_positive) VALUES ('SPICHAK, Mario', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('ADAMU, Edward', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('URIBE URIBE, Debra', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('CHIBLI, Charles', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('TOTOONOV, Amanda', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('ABDULNASSER, Michael', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('VERA LAZ, Brian', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('ZAITOUN, William', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('GOETZ, Benjamin', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('KLINGE, Ryan', false);

INSERT INTO customers (full_name, is_seeded_positive) VALUES ('Brandon Coleman', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('Becky Walker', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('Theresa Ray', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('William Williams', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('Donna Thomas', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('Eric Cabrera', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('Kevin Fowler', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('Joshua Brown', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('Daniel Becker', false);
INSERT INTO customers (full_name, is_seeded_positive) VALUES ('Ernest Richards', false);

COMMIT;
