# Gestor MD — checklist de deploy pós-correção

## 1. Descobrir onde os dados de verdade estão (faça isso primeiro)

No **Shell** do serviço Gestor-MD no Render, rode:

```bash
env | grep -i database
ls -la /data/ 2>/dev/null
```

- Se aparecer `DATABASE_URL=postgresql://...` **e** o app não estiver dando erro de conexão SQLite → produção já está em Postgres, mas rodando código antigo. Use a rota **A**.
- Se `/data/gestao_md.db` existir com tamanho > 0 → os dados reais estão em SQLite no disco. Use a rota **B**.

## Rota A — já está em Postgres (Neon antigo), só trocar de conta

Não precisa do `migrate_sqlite_to_postgres.py`. Use `pg_dump`/`pg_restore` direto entre os dois Neons:

```bash
# 1. Dump completo do Neon antigo (schema + dados)
pg_dump "postgresql://user:pass@host-antigo/dbname" -F c -f backup_gestor_md.dump

# 2. Restaura no Neon novo (conta paga)
pg_restore --no-owner --no-privileges -d "postgresql://user:pass@host-novo/dbname" backup_gestor_md.dump
```

Se não tiver `pg_dump`/`pg_restore` instalado localmente, dá pra rodar isso de dentro do Shell do Render (geralmente já tem cliente Postgres) ou de um Cloud Shell.

## Rota B — dados reais estão em SQLite no disco do Render

```bash
# 1. Baixe /data/gestao_md.db do Render pro seu computador (via Shell + download,
#    ou scp se o plano permitir)

# 2. Rode o script de migração
export DATABASE_URL="postgresql://user:pass@host-novo.neon.tech/dbname"
pip install psycopg2-binary
python migrate_sqlite_to_postgres.py /caminho/para/gestao_md.db
```

O script preserva os IDs originais e ajusta a sequência do Postgres pra continuar contando dali.

## 2. Variáveis de ambiente no Render (Settings → Environment)

| Variável | Valor | Obrigatória |
|---|---|---|
| `DATABASE_URL` | connection string do Neon **novo** (conta paga) | Sim |
| `JWT_SECRET` | uma string aleatória longa (ex: `openssl rand -hex 32`) | Sim |
| `ALLOWED_ORIGINS` | URL pública do seu serviço no Render (ex: `https://gestor-md.onrender.com`) | Recomendado |
| `RENDER` | já existe por padrão no Render, não precisa criar | — |
| `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_USER_ID` | se for usar OneDrive | Opcional |

Sem `JWT_SECRET` definido, o app usa uma chave padrão insegura — **defina antes de ir pra produção**.

## 3. Ping / keep-alive (evitar o Render dormir)

O endpoint `/api/ping` já foi adicionado (não toca no banco). Configure um monitor gratuito:

- [UptimeRobot](https://uptimerobot.com) ou [cron-job.org](https://cron-job.org)
- URL a monitorar: `https://SEU-APP.onrender.com/api/ping`
- Intervalo: a cada 10–14 minutos

## 4. Depois do primeiro deploy com o novo código

- Faça login uma vez com cada usuário existente — a senha em texto puro é migrada pra bcrypt automaticamente no primeiro login de cada um (não precisa reset manual).
- Troque a senha do usuário `admin` (a que estava commitada em texto puro no GitHub).
- Confirme que `/api/usuarios`, `/api/financeiro` etc. agora exigem token (teste sem header `Authorization` — deve dar 401).

## 5. Limpar o `gestao_md.db` do histórico do Git

Esse arquivo tem senhas reais em texto puro commitadas num repo público. Depois de migrar os dados:

```bash
# adiciona ao .gitignore
echo "gestao_md.db" >> .gitignore
echo "__pycache__/" >> .gitignore

# remove do histórico inteiro (requer git-filter-repo instalado)
git filter-repo --path gestao_md.db --invert-paths

git push origin main --force
```

Isso reescreve o histórico — avise se mais alguém tem esse repo clonado.
