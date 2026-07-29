"""
Migra os dados do gestao_md.db (SQLite) para um banco Postgres (Neon).

USE ISSO SE: a versão em produção no Render ainda estiver rodando em SQLite
(arquivo /data/gestao_md.db no disco do Render) e você quiser levar esses
registros pro Neon pago, sem perder nada.

NÃO USE ISSO SE: a produção já estiver de fato em Postgres/Neon — nesse caso
use o pg_dump/pg_restore descrito em NEON_MIGRATION.md (Neon -> Neon é mais
simples e não precisa desse script).

Como rodar:
    1. Baixe o gestao_md.db real de produção (via Shell do Render:
       o arquivo fica em /data/gestao_md.db)
    2. pip install psycopg2-binary
    3. export DATABASE_URL="postgresql://usuario:senha@host/dbname"
    4. python migrate_sqlite_to_postgres.py /caminho/para/gestao_md.db
"""
import sys
import sqlite3
import os
import psycopg2

TABELAS = ["usuarios", "ordens_servico", "financeiro"]


def criar_schema_postgres(pg_conn):
    cur = pg_conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            email TEXT,
            usuario TEXT NOT NULL UNIQUE,
            senha TEXT NOT NULL,
            perfil TEXT NOT NULL
        )
    ''')
    cur.execute('''
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
    cur.execute('''
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
    pg_conn.commit()


def migrar_tabela(sqlite_conn, pg_conn, tabela):
    scur = sqlite_conn.cursor()
    scur.execute(f"SELECT * FROM {tabela}")
    colunas = [d[0] for d in scur.description]
    linhas = scur.fetchall()

    if not linhas:
        print(f"  {tabela}: 0 registros (nada a migrar)")
        return

    pcur = pg_conn.cursor()
    placeholders = ", ".join(["%s"] * len(colunas))
    colunas_sql = ", ".join(colunas)
    insert_sql = f"INSERT INTO {tabela} ({colunas_sql}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"

    for linha in linhas:
        pcur.execute(insert_sql, linha)

    # Corrige a sequência do SERIAL pra continuar a partir do maior id migrado
    pcur.execute(f"SELECT setval(pg_get_serial_sequence('{tabela}', 'id'), COALESCE(MAX(id), 1)) FROM {tabela}")

    pg_conn.commit()
    print(f"  {tabela}: {len(linhas)} registros migrados")


def main():
    if len(sys.argv) != 2:
        print("Uso: python migrate_sqlite_to_postgres.py /caminho/para/gestao_md.db")
        sys.exit(1)

    sqlite_path = sys.argv[1]
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERRO: defina a variável de ambiente DATABASE_URL (connection string do Neon destino).")
        sys.exit(1)

    print(f"Origem (SQLite): {sqlite_path}")
    print(f"Destino (Postgres/Neon): {database_url.split('@')[-1]}")
    confirm = input("Confirma a migração? Isso vai INSERIR os dados no banco destino. (sim/nao): ")
    if confirm.strip().lower() != "sim":
        print("Cancelado.")
        sys.exit(0)

    sqlite_conn = sqlite3.connect(sqlite_path)
    pg_conn = psycopg2.connect(database_url, sslmode="require")

    print("\nCriando schema no Postgres (se ainda não existir)...")
    criar_schema_postgres(pg_conn)

    print("\nMigrando dados...")
    # Ordem importa por causa da FK id_tecnico -> usuarios
    for tabela in TABELAS:
        migrar_tabela(sqlite_conn, pg_conn, tabela)

    sqlite_conn.close()
    pg_conn.close()
    print("\nMigração concluída.")


if __name__ == "__main__":
    main()
