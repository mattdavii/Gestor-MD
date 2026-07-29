from fastapi import FastAPI, HTTPException, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import database
import auth
import pandas as pd
import os as os_sys
import shutil
import datetime
import onedrive_api  # Nosso novo robô

# --- LÓGICA DE AMBIENTE (NUVEM vs LOCAL) ---
# Se a variável RENDER existir, usa o disco montado, senão, pasta local.
IS_CLOUD = os_sys.environ.get("RENDER") is not None
BASE_DIR = "/data" if IS_CLOUD else "."

PASTA_DOCUMENTOS = os_sys.path.join(BASE_DIR, "Documentos_OS")
PASTA_EVIDENCIAS = os_sys.path.join(BASE_DIR, "Evidencias_OS")
os_sys.makedirs(PASTA_DOCUMENTOS, exist_ok=True)
os_sys.makedirs(PASTA_EVIDENCIAS, exist_ok=True)

# --- MIGRAÇÃO AUTOMÁTICA DE BANCO DE DADOS ---
def atualizar_banco():
    database.inicializar_banco()
    conn = database.conectar()

    if database.USE_POSTGRES:
        # Postgres suporta IF NOT EXISTS de forma idempotente, sem poluir a transação.
        conn.execute("ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS data_emissao DATE")
        conn.execute("ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS data_pagamento DATE")
        conn.execute("ALTER TABLE ordens_servico ADD COLUMN IF NOT EXISTS endereco TEXT")
        conn.execute("ALTER TABLE ordens_servico ADD COLUMN IF NOT EXISTS relatorio_tecnico TEXT")
    else:
        import sqlite3
        for stmt in [
            "ALTER TABLE financeiro ADD COLUMN data_emissao DATE",
            "ALTER TABLE financeiro ADD COLUMN data_pagamento DATE",
            "ALTER TABLE ordens_servico ADD COLUMN endereco TEXT",
            "ALTER TABLE ordens_servico ADD COLUMN relatorio_tecnico TEXT",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass

    conn.commit()
    conn.close()

atualizar_banco()

app = FastAPI(title="API Gestor MD Soluções")

# --- CORS RESTRITO ---
# Defina ALLOWED_ORIGINS no Render como lista separada por vírgula, ex:
# "https://gestor-md.onrender.com,http://localhost:8000"
_origins_env = os_sys.environ.get("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOWED_ORIGINS != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- MODELOS ---
class LoginRequest(BaseModel): usuario: str; senha: str
class FinanceiroRequest(BaseModel): empresa: str; descricao: str; valor: float; tipo: str; categoria: str; status_pagamento: str; status_nf: str; data_emissao: Optional[str] = None; data_pagamento: Optional[str] = None
class OSRequest(BaseModel): empresa: str; numero_os: str; cliente: str; plataforma: Optional[str] = ""; servico_descricao: str; id_tecnico: int; data_programada: str; endereco: Optional[str] = ""; relatorio_tecnico: Optional[str] = ""
class OSUpdateRequest(OSRequest): status: str
class UsuarioRequest(BaseModel): nome: str; email: Optional[str] = ""; usuario: str; senha: str; perfil: str
class UsuarioUpdateRequest(BaseModel): nome: str; email: Optional[str] = ""; perfil: str; senha: Optional[str] = ""
class StatusOSRequest(BaseModel): status: str; relatorio_tecnico: Optional[str] = ""

# --- KEEP-ALIVE (mesma técnica usada no app de Demandas) ---
# Aponte um monitor gratuito (UptimeRobot / cron-job.org) pra essa rota a cada 10-14 min.
# Não toca no banco de propósito, pra não gastar conexão do Neon à toa.
@app.get("/api/ping")
def ping():
    return {"status": "ok", "hora": datetime.datetime.utcnow().isoformat()}

# --- LOGIN E DASHBOARD ---
@app.post("/api/login")
def login(req: LoginRequest):
    conn = database.conectar()
    user = conn.execute(
        "SELECT id, nome, perfil, senha FROM usuarios WHERE usuario = ?", (req.usuario,)
    ).fetchone()

    if not user:
        conn.close()
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    user_id, nome, perfil, senha_armazenada = user

    # Migração transparente: se a senha ainda estiver em texto puro (banco antigo),
    # valida por comparação direta e re-grava já em hash bcrypt.
    if auth.senha_parece_hash(senha_armazenada):
        senha_ok = auth.verificar_senha(req.senha, senha_armazenada)
    else:
        senha_ok = (req.senha == senha_armazenada)
        if senha_ok:
            novo_hash = auth.hash_senha(req.senha)
            conn.execute("UPDATE usuarios SET senha = ? WHERE id = ?", (novo_hash, user_id))
            conn.commit()

    conn.close()

    if not senha_ok:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    token = auth.criar_token(user_id, perfil)
    return {"id": user_id, "nome": nome, "perfil": perfil, "token": token}

@app.get("/api/dashboard")
def get_dashboard(inicio: Optional[str] = None, fim: Optional[str] = None, user: dict = Depends(auth.get_current_user)):
    conn = database.conectar()
    filtro_fin = "WHERE status_pagamento = 'Pago'"
    filtro_os = "WHERE 1=1"
    params_fin, params_os = [], []

    if inicio and fim:
        filtro_fin += " AND date(data_pagamento) BETWEEN ? AND ?"
        filtro_os += " AND date(data_programada) BETWEEN ? AND ?"
        params_fin.extend([inicio, fim]); params_os.extend([inicio, fim])

    df_fin = pd.read_sql_query(f"SELECT valor, tipo, data_pagamento, data_registro FROM financeiro {filtro_fin}", conn._conn, params=params_fin)
    df_os = pd.read_sql_query(f"SELECT id, status FROM ordens_servico {filtro_os}", conn._conn, params=params_os)
    conn.close()

    entradas = float(df_fin[df_fin['tipo'] == 'Entrada']['valor'].sum()) if not df_fin.empty else 0.0
    saidas = float(df_fin[df_fin['tipo'] == 'Saída']['valor'].sum()) if not df_fin.empty else 0.0
    os_status = df_os['status'].value_counts().to_dict() if not df_os.empty else {}

    cat_entradas, cat_saidas = {}, {}
    if not df_fin.empty:
        df_fin['data_ref'] = df_fin['data_pagamento'].fillna(df_fin['data_registro']).astype(str).str[:10]
        fin_tempo = df_fin.groupby(['data_ref', 'tipo'])['valor'].sum().reset_index()
        for _, row in fin_tempo.iterrows():
            if row['tipo'] == 'Entrada': cat_entradas[row['data_ref']] = row['valor']
            else: cat_saidas[row['data_ref']] = row['valor']

    return {
        "faturamento_global": entradas, "despesas_globais": saidas, "caixa_real": entradas - saidas,
        "total_os": len(df_os), "grafico_os": os_status, "grafico_fin_entradas": cat_entradas, "grafico_fin_saidas": cat_saidas
    }

# --- FINANCEIRO (somente Admin) ---
@app.get("/api/financeiro")
def listar_financeiro(inicio: Optional[str] = None, fim: Optional[str] = None, user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar()
    query = "SELECT id, empresa, descricao, valor, tipo, status_pagamento, status_nf, categoria, data_registro, data_emissao, data_pagamento FROM financeiro"
    params = []
    if inicio and fim:
        query += " WHERE date(COALESCE(data_pagamento, data_registro)) BETWEEN ? AND ?"
        params.extend([inicio, fim])
    query += " ORDER BY id DESC"

    dados = conn.execute(query, params).fetchall()
    conn.close()
    return [{"id": d[0], "empresa": d[1], "descricao": d[2], "valor": d[3], "tipo": d[4], "status": d[5], "nf": d[6], "categoria": d[7], "data": d[8], "data_emissao": d[9], "data_pagamento": d[10]} for d in dados]

@app.post("/api/financeiro")
def criar_financeiro(req: FinanceiroRequest, user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar()
    conn.execute('INSERT INTO financeiro (empresa, descricao, valor, tipo, categoria, status_pagamento, status_nf, data_emissao, data_pagamento) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                 (req.empresa, req.descricao, req.valor, req.tipo, req.categoria, req.status_pagamento, req.status_nf, req.data_emissao, req.data_pagamento))
    conn.commit(); conn.close()
    return {"status": "sucesso"}

@app.put("/api/financeiro/{id}")
def atualizar_financeiro(id: int, req: FinanceiroRequest, user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar()
    conn.execute('UPDATE financeiro SET empresa=?, descricao=?, valor=?, tipo=?, categoria=?, status_pagamento=?, status_nf=?, data_emissao=?, data_pagamento=? WHERE id=?',
                 (req.empresa, req.descricao, req.valor, req.tipo, req.categoria, req.status_pagamento, req.status_nf, req.data_emissao, req.data_pagamento, id))
    conn.commit(); conn.close()
    return {"status": "sucesso"}

@app.delete("/api/financeiro/{id}")
def deletar_financeiro(id: int, user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar(); conn.execute("DELETE FROM financeiro WHERE id=?", (id,)); conn.commit(); conn.close()
    return {"status": "sucesso"}

# --- OS (autenticado; edição restrita a Admin) ---
@app.get("/api/os")
def listar_os(inicio: Optional[str] = None, fim: Optional[str] = None, user: dict = Depends(auth.get_current_user)):
    conn = database.conectar()
    query = '''SELECT os.id, os.empresa, os.numero_os, os.cliente, os.status, u.nome, os.data_programada, os.plataforma, os.id_tecnico, os.servico_descricao, os.endereco, os.relatorio_tecnico
               FROM ordens_servico os LEFT JOIN usuarios u ON os.id_tecnico = u.id'''
    params = []
    if inicio and fim:
        query += " WHERE date(os.data_programada) BETWEEN ? AND ?"
        params.extend([inicio, fim])
    query += " ORDER BY os.id DESC"

    dados = conn.execute(query, params).fetchall()
    conn.close()
    return [{"id": d[0], "empresa": d[1], "numero_os": d[2], "cliente": d[3], "status": d[4], "tecnico": d[5] or "N/A", "data": d[6], "plataforma": d[7], "id_tecnico": d[8], "descricao": d[9], "endereco": d[10], "relatorio": d[11]} for d in dados]

@app.post("/api/os")
def criar_os(req: OSRequest, user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar(); cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO ordens_servico (empresa, numero_os, cliente, plataforma, servico_descricao, id_tecnico, data_programada, endereco, relatorio_tecnico) '
        f'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?){database.LASTROWID_SQL}',
        (req.empresa, req.numero_os, req.cliente, req.plataforma, req.servico_descricao, req.id_tecnico, req.data_programada, req.endereco, req.relatorio_tecnico))
    os_id = cursor.lastrowid; conn.commit(); conn.close()
    return {"status": "sucesso", "id": os_id}

@app.put("/api/os/{id}")
def atualizar_os(id: int, req: OSUpdateRequest, user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar()
    conn.execute('UPDATE ordens_servico SET empresa=?, numero_os=?, cliente=?, plataforma=?, servico_descricao=?, id_tecnico=?, data_programada=?, endereco=?, status=?, relatorio_tecnico=? WHERE id=?',
                 (req.empresa, req.numero_os, req.cliente, req.plataforma, req.servico_descricao, req.id_tecnico, req.data_programada, req.endereco, req.status, req.relatorio_tecnico, id))
    conn.commit(); conn.close()
    return {"status": "sucesso", "id": id}

@app.delete("/api/os/{id}")
def deletar_os(id: int, user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar(); conn.execute("DELETE FROM ordens_servico WHERE id=?", (id,)); conn.commit(); conn.close()
    return {"status": "sucesso"}

@app.get("/api/minhas-os/{id_tecnico}")
def listar_minhas_os(id_tecnico: int, inicio: Optional[str] = None, fim: Optional[str] = None, user: dict = Depends(auth.get_current_user)):
    # Um técnico só pode ver as próprias OS; Admin pode ver de qualquer técnico.
    if user["perfil"] != "Admin" and user["id"] != id_tecnico:
        raise HTTPException(status_code=403, detail="Acesso negado.")
    conn = database.conectar()
    query = "SELECT id, numero_os, cliente, plataforma, servico_descricao, status, endereco, data_programada, relatorio_tecnico FROM ordens_servico WHERE id_tecnico=? AND status != 'Concluído'"
    params = [id_tecnico]
    if inicio and fim:
        query += " AND date(data_programada) BETWEEN ? AND ?"
        params.extend([inicio, fim])
    query += " ORDER BY data_programada ASC"

    dados = conn.execute(query, params).fetchall()
    conn.close()
    return [{"id": d[0], "numero_os": d[1], "cliente": d[2], "plataforma": d[3], "descricao": d[4], "status": d[5], "endereco": d[6], "data_programada": d[7], "relatorio": d[8]} for d in dados]

@app.put("/api/os/{id}/status")
def atualizar_status_os(id: int, req: StatusOSRequest, user: dict = Depends(auth.get_current_user)):
    conn = database.conectar(); conn.execute("UPDATE ordens_servico SET status=?, relatorio_tecnico=? WHERE id=?", (req.status, req.relatorio_tecnico, id)); conn.commit(); conn.close()
    return {"status": "sucesso"}

@app.get("/api/os/{id}/arquivos")
def listar_arquivos(id: int, user: dict = Depends(auth.get_current_user)):
    docs, evids = [], []
    pasta_d = os_sys.path.join(PASTA_DOCUMENTOS, f"OS_{id}")
    if os_sys.path.exists(pasta_d): docs = os_sys.listdir(pasta_d)
    pasta_e = os_sys.path.join(PASTA_EVIDENCIAS, f"OS_{id}")
    if os_sys.path.exists(pasta_e): evids = os_sys.listdir(pasta_e)
    return {"documentos": docs, "evidencias": evids}

# --- USUÁRIOS (somente Admin) ---
@app.get("/api/usuarios")
def listar_usuarios(user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar()
    dados = conn.execute("SELECT id, nome, email, usuario, perfil FROM usuarios").fetchall()
    conn.close()
    return [{"id": d[0], "nome": d[1], "email": d[2] or "", "usuario": d[3], "perfil": d[4]} for d in dados]

@app.get("/api/tecnicos")
def listar_tecnicos(user: dict = Depends(auth.get_current_user)):
    conn = database.conectar()
    dados = conn.execute("SELECT id, nome FROM usuarios WHERE perfil = 'Tecnico'").fetchall()
    conn.close()
    return [{"id": d[0], "nome": d[1]} for d in dados]

@app.post("/api/usuarios")
def criar_usuario(req: UsuarioRequest, user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar()
    try:
        senha_hash = auth.hash_senha(req.senha)
        conn.execute('INSERT INTO usuarios (nome, email, usuario, senha, perfil) VALUES (?, ?, ?, ?, ?)', (req.nome, req.email, req.usuario, senha_hash, req.perfil))
        conn.commit()
        return {"status": "sucesso"}
    except Exception:
        raise HTTPException(status_code=400, detail="Login já existe.")
    finally:
        conn.close()

@app.put("/api/usuarios/{id}")
def atualizar_usuario(id: int, req: UsuarioUpdateRequest, user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar()
    if req.senha and req.senha.strip() != "":
        senha_hash = auth.hash_senha(req.senha)
        conn.execute('UPDATE usuarios SET nome=?, email=?, perfil=?, senha=? WHERE id=?', (req.nome, req.email, req.perfil, senha_hash, id))
    else:
        conn.execute('UPDATE usuarios SET nome=?, email=?, perfil=? WHERE id=?', (req.nome, req.email, req.perfil, id))
    conn.commit(); conn.close()
    return {"status": "sucesso"}

@app.delete("/api/usuarios/{id}")
def deletar_usuario(id: int, user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    conn = database.conectar(); conn.execute("DELETE FROM usuarios WHERE id=?", (id,)); conn.commit(); conn.close()
    return {"status": "sucesso"}

# Monta arquivos estáticos para a logo funcionar
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Arquivo index.html não encontrado."

# === ROTAS DE ARQUIVOS (com OneDrive, com fallback local) ===
# Nota: as versões antigas dessas rotas (sem OneDrive) foram removidas —
# no FastAPI original, elas eram sobrescritas silenciosamente pelas de baixo,
# então já eram código morto.

@app.post("/api/os/{id}/documentos")
async def upload_documentos(id: int, arquivos: list[UploadFile] = File(...), user: dict = Depends(auth.get_current_user)):
    pasta_destino = f"Documentos_OS/OS_{id}"
    pasta_local = None

    if not onedrive_api.is_configured():
        pasta_local = os_sys.path.join(BASE_DIR, pasta_destino)
        os_sys.makedirs(pasta_local, exist_ok=True)

    for arquivo in arquivos:
        bytes_arq = await arquivo.read()
        if onedrive_api.is_configured():
            onedrive_api.upload_arquivo(bytes_arq, arquivo.filename, pasta_destino)
        else:
            with open(os_sys.path.join(pasta_local, arquivo.filename), "wb") as buffer:
                buffer.write(bytes_arq)
    return {"status": "sucesso"}

@app.post("/api/os/{id}/evidencias")
async def upload_evidencias(id: int, arquivos: list[UploadFile] = File(...), user: dict = Depends(auth.get_current_user)):
    pasta_destino = f"Evidencias_OS/OS_{id}"
    pasta_local = None

    if not onedrive_api.is_configured():
        pasta_local = os_sys.path.join(BASE_DIR, pasta_destino)
        os_sys.makedirs(pasta_local, exist_ok=True)

    for arquivo in arquivos:
        bytes_arq = await arquivo.read()
        if onedrive_api.is_configured():
            onedrive_api.upload_arquivo(bytes_arq, arquivo.filename, pasta_destino)
        else:
            with open(os_sys.path.join(pasta_local, arquivo.filename), "wb") as buffer:
                buffer.write(bytes_arq)
    return {"status": "sucesso"}

@app.get("/api/download/{tipo}/{id}/{arquivo}")
def baixar_arquivo(tipo: str, id: int, arquivo: str, user: dict = Depends(auth.get_current_user)):
    pasta = "Documentos_OS" if tipo == "documentos" else "Evidencias_OS"
    pasta_destino = f"{pasta}/OS_{id}"

    if onedrive_api.is_configured():
        link = onedrive_api.get_download_link(arquivo, pasta_destino)
        if link: return RedirectResponse(url=link)
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no OneDrive")
    else:
        caminho = os_sys.path.join(BASE_DIR, pasta_destino, arquivo)
        if os_sys.path.exists(caminho): return FileResponse(path=caminho, filename=arquivo)
        raise HTTPException(status_code=404, detail="Arquivo não encontrado localmente")

# === BACKUP DO BANCO PARA O ONEDRIVE (somente Admin) ===
@app.get("/api/backup")
def fazer_backup_banco(user: dict = Depends(auth.get_current_user)):
    auth.exigir_admin(user)
    if not onedrive_api.is_configured():
        raise HTTPException(status_code=400, detail="OneDrive não está configurado.")

    if database.USE_POSTGRES:
        raise HTTPException(status_code=400, detail="Backup automático via /api/backup é só para SQLite. Em Postgres/Neon, use os backups automáticos do Neon (Point-in-time restore) ou pg_dump agendado.")

    db_path = "/data/gestao_md.db" if IS_CLOUD else "gestao_md.db"

    try:
        with open(db_path, "rb") as f:
            db_bytes = f.read()
        data_atual = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        nome_backup = f"gestao_md_backup_{data_atual}.db"

        onedrive_api.upload_arquivo(db_bytes, nome_backup, "Backups_BD")
        return {"status": f"Backup {nome_backup} enviado com sucesso para o OneDrive!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
