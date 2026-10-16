-- ============================================================
-- Perpetual Bistro - SQLite Schema & Seed Data
-- Shelly Virtual Waiter - Lesson 6 Lab
-- ============================================================
-- Run with:  python init_db.py
-- Produces:  restaurant.db  (in this same folder)
-- ============================================================

DROP TABLE IF EXISTS order_line_items;
DROP TABLE IF EXISTS table_sessions;
DROP TABLE IF EXISTS menu_items;

-- ------------------------------------------------------------
-- MENU ITEMS (source of truth for menu, price, stock,
--             ingredients, and recorded allergens)
-- ------------------------------------------------------------
CREATE TABLE menu_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    category        TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    ingredients     TEXT    NOT NULL DEFAULT 'not available',
    price           REAL    NOT NULL CHECK (price >= 0),
    stock_qty       INTEGER NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    allergens       TEXT    NOT NULL DEFAULT 'none'
);

-- ------------------------------------------------------------
-- TABLE SESSIONS
-- ------------------------------------------------------------
CREATE TABLE table_sessions (
    session_id      TEXT PRIMARY KEY,
    table_no        INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('OPEN','REVIEWING','CONFIRMED','CANCELLED')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    confirmed_at    TEXT
);

-- ------------------------------------------------------------
-- ORDER LINE ITEMS
-- ------------------------------------------------------------
CREATE TABLE order_line_items (
    line_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,
    item_id             INTEGER NOT NULL,
    quantity            INTEGER NOT NULL CHECK (quantity > 0),
    special_instructions TEXT DEFAULT '',
    added_at            TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES table_sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (item_id)    REFERENCES menu_items(id)
);

CREATE INDEX idx_order_session ON order_line_items(session_id);
CREATE INDEX idx_menu_category ON menu_items(category);

-- ============================================================
-- SEED DATA - 16 MENU ITEMS
-- ============================================================

-- APPETIZERS
INSERT INTO menu_items (name, category, description, ingredients, price, stock_qty, allergens) VALUES
('Crispy Lumpia Shanghai', 'Appetizers',
 'Crispy pork spring rolls served with sweet chili dip',
 'Pork, carrots, onions, spring roll wrapper, sweet chili dip',
 120.00, 20, 'gluten'),
('Calamari Fritters', 'Appetizers',
 'Golden fried squid rings with calamansi aioli',
 'Squid, flour batter, calamansi, garlic aioli, cooking oil',
 180.00, 15, 'gluten,seafood'),
('Vegetable Spring Rolls', 'Appetizers',
 'Fresh vegetable rolls with peanut sauce',
 'Cabbage, carrots, peanuts, rice paper wrapper, peanut sauce',
 110.00,  0, 'peanuts,gluten');

-- MAIN DISHES
INSERT INTO menu_items (name, category, description, ingredients, price, stock_qty, allergens) VALUES
('Chicken Adobo', 'Main Dishes',
 'Classic Filipino chicken braised in soy sauce and vinegar',
 'Chicken, soy sauce, vinegar, garlic, bay leaves, peppercorns',
 220.00, 25, 'soy'),
('Beef Kare-Kare', 'Main Dishes',
 'Oxtail stew in rich peanut sauce with vegetables',
 'Oxtail beef, peanut sauce, banana blossom, string beans, bok choy, bagoong shrimp paste',
 320.00, 12, 'peanuts,seafood'),
('Pork Sinigang', 'Main Dishes',
 'Sour tamarind soup with pork and fresh vegetables',
 'Pork, tamarind, kangkong, radish, tomato, onion, string beans',
 250.00, 18, 'none'),
('Grilled Bangus', 'Main Dishes',
 'Marinated milkfish grilled with calamansi',
 'Milkfish, calamansi, garlic, soy sauce',
 280.00,  0, 'fish,soy'),
('Vegetable Pad Thai', 'Main Dishes',
 'Stir-fried rice noodles with tofu and peanuts',
 'Rice noodles, tofu, peanuts, bean sprouts, egg, tamarind sauce',
 200.00, 10, 'peanuts,soy,eggs');

-- RICE
INSERT INTO menu_items (name, category, description, ingredients, price, stock_qty, allergens) VALUES
('Steamed White Rice', 'Rice',
 'Fluffy steamed jasmine rice',
 'Jasmine rice, water',
 30.00, 100, 'none'),
('Garlic Fried Rice', 'Rice',
 'Sinangag - garlic fried rice',
 'Rice, garlic, cooking oil, salt',
 60.00,  80, 'none');

-- DRINKS
INSERT INTO menu_items (name, category, description, ingredients, price, stock_qty, allergens) VALUES
('Iced Tea', 'Drinks',
 'House-brewed iced tea with lemon',
 'Black tea, water, lemon, sugar',
 50.00,  50, 'none'),
('Fresh Buko Juice', 'Drinks',
 'Chilled fresh coconut water',
 'Coconut water',
 80.00,  30, 'none'),
('Calamansi Juice', 'Drinks',
 'Refreshing calamansi citrus drink',
 'Calamansi, water, sugar',
 70.00,  40, 'none');

-- DESSERTS
INSERT INTO menu_items (name, category, description, ingredients, price, stock_qty, allergens) VALUES
('Halo-Halo', 'Desserts',
 'Shaved ice dessert with ube, leche flan, and fruits',
 'Shaved ice, evaporated milk, ube, leche flan, sweet beans, jackfruit',
 150.00, 15, 'dairy,eggs'),
('Leche Flan', 'Desserts',
 'Creamy caramel custard',
 'Egg yolks, condensed milk, caramel, vanilla',
 120.00, 20, 'dairy,eggs'),
('Mango Graham', 'Desserts',
 'Layered mango, graham crackers, and cream',
 'Mango, graham crackers, cream, condensed milk',
 130.00,  2, 'gluten,dairy');