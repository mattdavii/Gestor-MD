import os

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3

# Usado pelo main.py para pegar o id recem-inserido de forma compativel
# com Postgres (que exige RETURNING id) e SQLite (que ja tem lastrowid nativo).
LASTROWID_SQL = " RETURNING id" if USE_POSTGRES else ""


class Cursor:
    """Envelopa o cursor real (sqlite3 ou psycopg2) pra dar uma interface unica."""

    def __init__(self, raw_cursor, is_pg):
        self._c = raw_cursor
        self._pg = is_pg
        self.lastrowid = None

    def execute(self, query, params=None):
        params = params or []
        q = query.replace("?", "%s") if self._pg else query
        self._c.execute(q, params)
        if self._pg:
            if "RETURNING" in q.upper():
                row = self._c.fetchone()
                self.lastrowid = row[0] if row else None
        else:
            self.lastrowid = self._c.lastrowid
        return self

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()


class Connection:
    """Envelopa a conexao real pra expor .execute() no estilo sqlite3."""

    def __init__(self, raw_conn, is_pg):
        self._conn = raw_conn
        self._pg = is_pg

    def cursor(self):
        return Cursor(self._conn.cursor(), self._pg)

    def execute(self, query, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def conectar():
    if USE_POSTGRES:
        raw = psycopg2.connect(DATABASE_URL, sslmode="require")
        return Connection(raw, True)
    else:
        DB_PATH = "/data/gestao_md.db" if os.environ.get("RENDER") else "gestao_md.db"
        raw = sqlite3.connect(DB_PATH)
        return Connection(raw, False)


def inicializar_banco():
    conn = conectar()
    cursor = conn.cursor()

    if USE_POSTGRES:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT,
                usuario TEXT NOT NULL UNIQUE,
                senha TEXT NOT NULL,
                perfil TEXT NOT NULL
            )
        ''')
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                email TEXT,
                usuario TEXT NOT NULL UNIQUE,
                senha TEXT NOT NULL,
                perfil TEXT NOT NULL
            )
        ''')

    # Cria o usuario Admin padrao se nao existir (senha ja em hash bcrypt)
    cursor.execute("SELECT id FROM usuarios WHERE usuario = 'admin'")
    if not cursor.fetchone():
        import auth
        senha_hash = auth.hash_senha("admin123")
        cursor.execute(
            "INSERT INTO usuarios (nome, usuario, senha, perfil) VALUES (?, ?, ?, ?)",
            ("Administrador", "admin", senha_hash, "Admin"),
        )

    if USE_POSTGRES:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ordens_servico (
                id SERIAL PRIMARY KEY,
                empresa TEXT NOT NULL,
                numero_os TEXT,
                cliente TEXT NOT NULL,
                plataforma TEXT,
                endereco TEXT,
                servico_descricao TEXT NOT NULL,
                relatorio_tecnico TEXT,
                status TEXT DEFAULT 'Pendente',
                id_tecnico INTEGER REFERENCES usuarios(id),
                data_programada DATE,
                data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS financeiro (
                id SERIAL PRIMARY KEY,
                empresa TEXT NOT NULL,
                descricao TEXT NOT NULL,
                valor REAL NOT NULL,
                tipo TEXT NOT NULL,
                categoria TEXT,
                status_pagamento TEXT DEFAULT 'Pendente',
                status_nf TEXT DEFAULT 'Pendente',
                data_emissao DATE,
                data_pagamento DATE,
                data_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ordens_servico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa TEXT NOT NULL,
                numero_os TEXT,
                cliente TEXT NOT NULL,
                plataforma TEXT,
                endereco TEXT,
                servico_descricao TEXT NOT NULL,
                relatorio_tecnico TEXT,
                status TEXT DEFAULT 'Pendente',
                id_tecnico INTEGER,
                data_programada DATE,
                data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (id_tecnico) REFERENCES usuarios(id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS financeiro (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empresa TEXT NOT NULL,
                descricao TEXT NOT NULL,
                valor REAL NOT NULL,
                tipo TEXT NOT NULL,
                categoria TEXT,
                status_pagamento TEXT DEFAULT 'Pendente',
                status_nf TEXT DEFAULT 'Pendente',
                data_emissao DATE,
                data_pagamento DATE,
                data_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

    conn.commit()
    conn.close()
