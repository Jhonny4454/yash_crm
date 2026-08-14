-- ===========================================================================
--  migrations_api.sql
--  Tables added by the REST API / customer-portal release.
--
--  Run this once against an EXISTING production database (MySQL 8 / MariaDB).
--  On a fresh install `db.create_all()` (init_database in app.py) already
--  creates these, so this file is only needed when upgrading in place.
--
--    mysql -h HOST -u USER -p DBNAME < mysql/migrations_api.sql
--
--  Every statement is written to be safe to re-run.
-- ===========================================================================

-- ---------------------------------------------------------------------------
--  Push notification device registry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS device_tokens (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    customer_id  INT          NOT NULL,
    token        VARCHAR(255) NOT NULL,
    platform     VARCHAR(20)  DEFAULT 'android',
    provider     VARCHAR(20)  DEFAULT 'expo',
    app_version  VARCHAR(20)  NULL,
    is_active    TINYINT(1)   DEFAULT 1,
    created_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_device_token (customer_id, token),
    KEY ix_device_tokens_customer (customer_id),
    KEY ix_device_tokens_token (token),
    KEY ix_device_tokens_active (is_active),
    CONSTRAINT fk_device_tokens_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
--  Notification templates
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_templates (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    code          VARCHAR(50)  NOT NULL,
    name          VARCHAR(100) NOT NULL,
    title         VARCHAR(150) DEFAULT '',
    body          TEXT,
    description   VARCHAR(255) NULL,
    channel       VARCHAR(20)  DEFAULT 'push',
    send_push     TINYINT(1)   DEFAULT 1,
    send_whatsapp TINYINT(1)   DEFAULT 0,
    is_active     TINYINT(1)   DEFAULT 1,
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_notification_template_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
--  Delivered / queued notifications
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    customer_id   INT          NOT NULL,
    template_code VARCHAR(50)  NULL,
    title         VARCHAR(150) NULL,
    body          TEXT,
    channel       VARCHAR(20)  DEFAULT 'push',
    status        VARCHAR(20)  DEFAULT 'queued',
    error         VARCHAR(500) NULL,
    is_read       TINYINT(1)   DEFAULT 0,
    read_at       DATETIME     NULL,
    sent_at       DATETIME     NULL,
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    KEY ix_notifications_customer (customer_id),
    KEY ix_notifications_status (status),
    KEY ix_notifications_read (is_read),
    KEY ix_notifications_created (created_at),
    KEY ix_notifications_code (template_code),
    CONSTRAINT fk_notifications_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
--  Seed the five standard notification templates
--  (app.py also does this on boot; harmless to run both.)
-- ---------------------------------------------------------------------------
INSERT INTO notification_templates (code, name, title, body, description)
SELECT * FROM (SELECT
    'plan_expiring',
    'Plan expiring soon',
    'Your plan expires in {days} days',
    'Hi {customer_name}, your {plan_name} plan expires on {end_date}. Renew from the app to stay connected.',
    'Sent 7/3/1 days before expiry.') AS t
WHERE NOT EXISTS (SELECT 1 FROM notification_templates WHERE code = 'plan_expiring');

INSERT INTO notification_templates (code, name, title, body, description)
SELECT * FROM (SELECT
    'plan_expired',
    'Plan expired',
    'Your plan has expired',
    'Hi {customer_name}, your {plan_name} plan expired on {end_date}. Renew now to restore your connection.',
    'Sent on the expiry date.') AS t
WHERE NOT EXISTS (SELECT 1 FROM notification_templates WHERE code = 'plan_expired');

INSERT INTO notification_templates (code, name, title, body, description)
SELECT * FROM (SELECT
    'payment_received',
    'Payment received',
    'Payment received - Rs {amount}',
    'Thank you {customer_name}. We have received Rs {amount} against invoice {invoice_no}.',
    'Sent when a payment is recorded or a gateway payment succeeds.') AS t
WHERE NOT EXISTS (SELECT 1 FROM notification_templates WHERE code = 'payment_received');

INSERT INTO notification_templates (code, name, title, body, description)
SELECT * FROM (SELECT
    'invoice_generated',
    'New invoice',
    'New invoice {invoice_no}',
    'Hi {customer_name}, invoice {invoice_no} for Rs {amount} is due on {due_date}. Pay from the app.',
    'Sent when a new invoice is raised.') AS t
WHERE NOT EXISTS (SELECT 1 FROM notification_templates WHERE code = 'invoice_generated');

INSERT INTO notification_templates (code, name, title, body, description)
SELECT * FROM (SELECT
    'plan_changed',
    'Plan changed',
    'Plan updated to {plan_name}',
    'Hi {customer_name}, your plan is now {plan_name}, valid until {end_date}.',
    'Sent after a successful plan change.') AS t
WHERE NOT EXISTS (SELECT 1 FROM notification_templates WHERE code = 'plan_changed');


-- ---------------------------------------------------------------------------
--  Addon Invoice categories used by the new "Addon Charges" screen.
--  Safe to skip if you already manage these from Masters.
-- ---------------------------------------------------------------------------
INSERT INTO addon_categories (name, default_price, description, is_active)
SELECT * FROM (SELECT 'Installation Charges', 0.00, 'New connection installation', 1) AS t
WHERE NOT EXISTS (SELECT 1 FROM addon_categories WHERE name = 'Installation Charges');

INSERT INTO addon_categories (name, default_price, description, is_active)
SELECT * FROM (SELECT 'Shifting Charges', 0.00, 'Relocating an existing connection', 1) AS t
WHERE NOT EXISTS (SELECT 1 FROM addon_categories WHERE name = 'Shifting Charges');

INSERT INTO addon_categories (name, default_price, description, is_active)
SELECT * FROM (SELECT 'Reconnection Charges', 0.00, 'Re-enabling a disconnected line', 1) AS t
WHERE NOT EXISTS (SELECT 1 FROM addon_categories WHERE name = 'Reconnection Charges');

INSERT INTO addon_categories (name, default_price, description, is_active)
SELECT * FROM (SELECT 'ONT Device', 0.00, 'Optical network terminal', 1) AS t
WHERE NOT EXISTS (SELECT 1 FROM addon_categories WHERE name = 'ONT Device');

INSERT INTO addon_categories (name, default_price, description, is_active)
SELECT * FROM (SELECT 'ONU Device', 0.00, 'Optical network unit', 1) AS t
WHERE NOT EXISTS (SELECT 1 FROM addon_categories WHERE name = 'ONU Device');

INSERT INTO addon_categories (name, default_price, description, is_active)
SELECT * FROM (SELECT 'Router / Wi-Fi Device', 0.00, 'Customer premises router', 1) AS t
WHERE NOT EXISTS (SELECT 1 FROM addon_categories WHERE name = 'Router / Wi-Fi Device');

INSERT INTO addon_categories (name, default_price, description, is_active)
SELECT * FROM (SELECT 'Cable / Patch Cord', 0.00, 'Fibre, patch cords and accessories', 1) AS t
WHERE NOT EXISTS (SELECT 1 FROM addon_categories WHERE name = 'Cable / Patch Cord');

INSERT INTO addon_categories (name, default_price, description, is_active)
SELECT * FROM (SELECT 'Other Charges', 0.00, 'Anything not covered above', 1) AS t
WHERE NOT EXISTS (SELECT 1 FROM addon_categories WHERE name = 'Other Charges');
