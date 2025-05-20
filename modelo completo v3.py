from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from openai import OpenAI
from datetime import datetime
from urllib.parse import quote
from supabase import create_client
import os

# Carrega variáveis de ambiente (Render usará ENV diretamente)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUCKET = "peticoesgeradas"

# Inicializa clientes
client = OpenAI(api_key=OPENAI_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Inicializa FastAPI
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conta tokens simples
def contar_tokens(texto):
    return len(texto.split())

# Gera nome de arquivo único
def gerar_nome_arquivo(dados_usuario):
    nome_base = dados_usuario.get('reclamante', 'anonimo').replace(' ', '_')
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"peticao_{nome_base}_{timestamp}.docx"

# IA reescreve parágrafos com base no contexto
def reescrever_com_ia(paragrafos, contexto_usuario: dict):
    texto_final = ""
    custo_total = 0
    contexto = "\n".join([f"{chave}: {valor}" for chave, valor in contexto_usuario.items()])
    secao_atual = ""

    for par in paragrafos:
        if not par.strip():
            texto_final += "\n"
            continue

        texto = par.strip()

        if texto.isupper() and len(texto) < 100:
            secao_atual = texto
            texto_final += texto + "\n\n"
            continue

        modelo = "gpt-4" if any(s in secao_atual for s in ["FATOS", "FUNDAMENTAÇÃO", "PEDIDOS"]) else "gpt-3.5-turbo"

        prompt = (
            f"Você é um advogado redigindo uma petição trabalhista.\n"
            f"Abaixo estão os dados fornecidos pelo cliente:\n\n{contexto}\n\n"
            f"Seção: {secao_atual or 'Geral'}\n\n"
            f"Reescreva o seguinte parágrafo com linguagem jurídica clara, formal e adaptada:\n\n{par}"
        )

        try:
            resposta = client.chat.completions.create(
                model=modelo,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4
            )
            texto_revisado = resposta.choices[0].message.content.strip()
            print(f"\n--- [{modelo}] {secao_atual} ---\n→ {texto_revisado[:100]}...\n")
        except Exception as e:
            texto_revisado = f"[ERRO IA] {par}"
            print(f"Erro: {e}")

        texto_final += texto_revisado + "\n\n"

        tokens_in = contar_tokens(prompt)
        tokens_out = contar_tokens(texto_revisado)
        custo = (tokens_in / 1000) * (0.01 if modelo == "gpt-4" else 0.0005) + \
                (tokens_out / 1000) * (0.03 if modelo == "gpt-4" else 0.0015)
        custo_total += custo

    return texto_final.strip(), custo_total

# Formata e salva documento
def formatar_documento_visualmente(texto_formatado, caminho_saida):
    doc = Document()

    for par in texto_formatado.split("\n"):
        texto = par.strip()
        if not texto:
            doc.add_paragraph("")
            continue
        if texto.isupper() and len(texto) < 100:
            p = doc.add_paragraph()
            run = p.add_run(texto)
            run.bold = True
            run.font.size = Pt(14)
            run.font.name = 'Times New Roman'
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        else:
            p = doc.add_paragraph()
            run = p.add_run(texto)
            run.font.name = 'Times New Roman'
            run.font.size = Pt(12)
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    footer = doc.sections[0].footer.paragraphs[0]
    footer.text = "IBOTI ADVOCACIA – OAB/RS 65.382 | www.ibotiadvogados.com.br | (51) 98418-2882"
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.save(caminho_saida)

# Envia para Supabase
def enviar_para_supabase(caminho_local, nome_arquivo):
    with open(caminho_local, "rb") as f:
        dados = f.read()

    supabase.storage.from_(BUCKET).upload(
        nome_arquivo,
        dados,
        {"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    )

    url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{quote(nome_arquivo)}"
    return url

# Endpoint principal
@app.post("/gerar-peticao")
async def gerar_peticao(request: Request):
    dados = await request.json()

    nome_arquivo = gerar_nome_arquivo(dados)
    caminho_saida = f"/tmp/{nome_arquivo}"  # Pasta segura no Render

    # Use seu modelo docx no Supabase ou crie texto direto (aqui é gerado do zero via IA)
    paragrafos = [
        "EXCELENTÍSSIMO SENHOR DOUTOR JUIZ DO TRABALHO DA VARA DO TRABALHO DE [CIDADE].",
        "FATOS",
        "O Reclamante laborou na empresa por mais de 5 anos, exercendo função de motorista.",
        "FUNDAMENTAÇÃO",
        "O vínculo empregatício está comprovado por meio dos documentos anexos.",
        "PEDIDOS",
        "Requer o pagamento das verbas rescisórias, horas extras e FGTS não depositado."
    ]

    texto_revisado, custo = reescrever_com_ia(paragrafos, dados)
    formatar_documento_visualmente(texto_revisado, caminho_saida)
    url_download = enviar_para_supabase(caminho_saida, nome_arquivo)

    return {
        "arquivo": nome_arquivo,
        "url_download": url_download,
        "custo_estimado_usd": round(custo, 4)
    }

@app.options("/gerar-peticao")
async def options_gerar_peticao():
    return {"status": "ok"}
