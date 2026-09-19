-- Runs once, when the MariaDB data volume is first created.
-- The test runner creates and drops its own `test_catalog` database, which the
-- application user is not allowed to do by default.
GRANT ALL PRIVILEGES ON `test\_catalog%`.* TO 'catalog'@'%';
FLUSH PRIVILEGES;
