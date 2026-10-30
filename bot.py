import os
import re
import io
import urllib.parse
from datetime import datetime
from PIL import Image
import requests
import google.generativeai as genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# --- CHAVES DE ACESSO ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "SEU_TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "SUA_CHAVE_GEMINI_API")
FIPE_API_TOKEN = os.getenv("FIPE_API_TOKEN", "SEU_TOKEN_FIPE_API")

genai.configure(api_key=GEMINI_API_KEY)

# Estados da Conversa
RECEBE_NOME, RECEBE_TELEFONE, ESCOLHE_FIPE = range(3)

# Tabela de Tarifas (Rio de Janeiro)
TABELA_VALORES = [
    {"max": 11000, "faixa": "Até 11k", "mensal": 109.90, "desconto": 109.90},
    {"max": 21000, "faixa": "11k a 21k", "mensal": 129.90, "desconto": 116.90},
    {"max": 31000, "faixa": "21k a 31k", "mensal": 159.90, "desconto": 143.90},
    {"max": 41000, "faixa": "31k a 41k", "mensal": 189.90, "desconto": 170.90},
    {"max": 51000, "faixa": "41k a 51k", "mensal": 229.90, "desconto": 206.90},
    {"max": 61000, "faixa": "51k a 61k", "mensal": 259.90, "desconto": 233.90},
    {"max": 71000, "faixa": "61k a 71k", "mensal": 289.90, "desconto": 260.90},
    {"max": 81000, "faixa": "71k a 81k", "mensal": 309.90, "desconto": 278.90},
    {"max": 120000, "faixa": "81k a 120k", "mensal": 349.90, "desconto": 314.90},
]

def obter_faixa_preco(valor_fipe: float):
    for item in TABELA_VALORES:
        if valor_fipe <= item["max"]:
            return item
    return {"faixa": "Acima de 120k (Sob Consulta)", "mensal": 0.0, "desconto": 0.0}

def saudacao_periodo():
    hora = datetime.now().hour
    if 5 <= hora < 12:
        return "Bom dia"
    elif 12 <= hora < 18:
        return "Boa tarde"
    return "Boa noite"

def extrair_placa_imagem(imagem_bytes):
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        img = Image.open(io.BytesIO(imagem_bytes))
        prompt = (
            "Identifique e retorne APENAS a placa do veículo nesta imagem no formato padrão ou Mercosul "
            "(apenas letras e números, sem hífens ou pontuações). Se não encontrar placa, retorne VAZIO."
        )
        resposta = model.generate_content([prompt, img])
        texto = resposta.text.strip().upper()
        match = re.search(r"[A-Z]{3}[0-9][0-9A-Z][0-9]{2}", texto)
        return match.group(0) if match else None
    except Exception as e:
        print(f"Erro no OCR: {e}")
        return None

def consultar_placa_fipe_api(placa: str):
    """
    Consulta o endpoint de placas da fipe.api.br
    Retorna:
      - 'marca', 'modelo_base', 'ano_fab', 'ano_mod'
      - 'opcoes_fipe': lista de dicts com {'codigo', 'modelo', 'valor', 'referencia'}
    """
    url = f"https://fipe.api.br/v2/plates/{placa}"
    headers = {
        "Authorization": f"Bearer {FIPE_API_TOKEN}",
        "Accept": "application/json"
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            res = r.json()
            marca = res.get("brand", "INDEFINIDA")
            modelo_base = res.get("model", "VEÍCULO")
            ano_fab = str(res.get("yearManufacture", "----"))
            ano_mod = str(res.get("yearModel", "----"))
            
            fipes_raw = res.get("fipe", [])
            opcoes = []
            
            # Se vier dict único ou lista
            if isinstance(fipes_raw, dict):
                fipes_raw = [fipes_raw]
                
            for item in fipes_raw:
                val = item.get("price", 0.0)
                if isinstance(val, str):
                    val = float(val.replace("R$", "").replace(".", "").replace(",", ".").strip())
                opcoes.append({
                    "codigo": item.get("fipeCode", item.get("code", "000000-0")),
                    "modelo": item.get("model", modelo_base),
                    "valor": float(val),
                    "referencia": item.get("referenceMonth", "Mês Atual")
                })
                
            if opcoes:
                return {
                    "marca": marca,
                    "modelo_base": modelo_base,
                    "ano_fab": ano_fab,
                    "ano_mod": ano_mod,
                    "opcoes": opcoes
                }
    except Exception as e:
        print(f"Erro na requisição fipe.api: {e}")

    # Mock de teste automático (com 2 opções de FIPE para testar a exceção)
    return {
        "marca": "FIAT",
        "modelo_base": "SIENA",
        "ano_fab": "2002",
        "ano_mod": "2003",
        "opcoes": [
            {
                "codigo": "001187-8",
                "modelo": "SIENA FIRE 1.0 8V",
                "valor": 12995.80,
                "referencia": "Outubro de 2026"
            },
            {
                "codigo": "001210-6",
                "modelo": "SIENA ELX 1.3 16V",
                "valor": 15420.00,
                "referencia": "Outubro de 2026"
            }
        ]
    }

# --- CRIAÇÃO DO TEXTO E LINK FINAL ---

def montar_resposta_cotacao(user_data: dict, fipe_escolhida: dict) -> str:
    nome = user_data["nome_cliente"]
    placa = user_data["placa"]
    tel = user_data["telefone"]
    marca = user_data["dados_veiculo"]["marca"]
    ano_fab = user_data["dados_veiculo"]["ano_fab"]
    ano_mod = user_data["dados_veiculo"]["ano_mod"]

    modelo_fipe = fipe_escolhida["modelo"]
    codigo_fipe = fipe_escolhida["codigo"]
    valor_fipe = fipe_escolhida["valor"]
    mes_ref = fipe_escolhida["referencia"]

    faixa_info = obter_faixa_preco(valor_fipe)
    valor_mensal = f"R${faixa_info['mensal']:.2f}".replace(".", ",")
    valor_desc = f"R${faixa_info['desconto']:.2f}".replace(".", ",")
    valor_fipe_fmt = f"R${valor_fipe:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    saudacao = saudacao_periodo()
    modelo_ano = f"{modelo_fipe} {ano_fab}/{ano_mod}"
    
    texto_zap = (
        f"{saudacao}, {nome}! Tudo bem?\n\n"
        f"Nos falamos hoje mais cedo no sinal da Av. Maricá. Me confirma uma coisa: "
        f"seu {modelo_ano} é manual ou automático?\n\n"
        f"Modelo: *{modelo_ano.upper()}*\n"
        f"Placa: *{placa}*\n\n"
        f"Com essa confirmação, consigo dar continuidade à sua cotação!"
    )
    
    link_wa = f"https://wa.me/{tel}?text={urllib.parse.quote(texto_zap)}"

    # Linha formatada para a agenda: Nome em maiúsculo + Modelo + Ano + Placa
    # O uso de crases `texto` no Telegram permite copiar com 1 toque no celular
    contato_agenda = f"{nome.upper()} {modelo_fipe.upper()} {ano_mod} {placa}"

    return (
        f"📋 *Cotação para {modelo_fipe} {ano_mod} ({codigo_fipe}) de {nome}:*\n\n"
        f"📇 *Salvar contato (toque para copiar):*\n"
        f"`{contato_agenda}`\n\n"
        f"🚗 *Veículo:* {marca} / {modelo_fipe}\n"
        f"📅 *Ano:* {ano_fab} / {ano_mod}\n"
        f"🔢 *Placa:* {placa}\n\n"
        f"💰 *Valor da FIPE:* {valor_fipe_fmt}\n"
        f"📊 *Base:* {mes_ref}\n\n"
        f"🏷️ *Faixa:* {faixa_info['faixa']}\n"
        f"💵 *Valor mensal:* {valor_mensal}\n"
        f"⚡ *Valor com desconto:* {valor_desc}\n\n"
        f"📲 *Mandar primeira mensagem:*\n{link_wa}"
    )


# --- HANDLERS DO TELEGRAM ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Olá! Envie a **foto da placa** ou **digite a placa** do veículo para começar."
    )

async def receber_entrada_placa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    placa = None
    
    if update.message.photo:
        await update.message.reply_text("🔍 Identificando placa na foto...")
        foto = await update.message.photo[-1].get_file()
        foto_bytes = await foto.download_as_bytearray()
        placa = extrair_placa_imagem(foto_bytes)
        if not placa:
            await update.message.reply_text("❌ Placa não identificada. Digite a placa em texto:")
            return ConversationHandler.END
    else:
        texto = update.message.text.strip().upper().replace("-", "").replace(" ", "")
        match = re.search(r"[A-Z]{3}[0-9][0-9A-Z][0-9]{2}", texto)
        if match:
            placa = match.group(0)
        else:
            await update.message.reply_text("⚠️ Placa inválida. Digite no formato ABC1234 ou ABC1D23:")
            return ConversationHandler.END

    context.user_data["placa"] = placa
    await update.message.reply_text(f"✅ Placa: *{placa}*\n\nQual é o **Nome do Cliente**?", parse_mode="Markdown")
    return RECEBE_NOME

async def receber_nome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nome_cliente"] = update.message.text.strip()
    await update.message.reply_text("Informe o **WhatsApp do cliente** com DDD (ex: 21999999999):")
    return RECEBE_TELEFONE

async def receber_telefone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tel_raw = re.sub(r"\D", "", update.message.text.strip())
    if len(tel_raw) < 10:
        await update.message.reply_text("⚠️ Número inválido. Digite com DDD (ex: 21988887777):")
        return RECEBE_TELEFONE

    context.user_data["telefone"] = f"55{tel_raw}" if not tel_raw.startswith("55") else tel_raw
    placa = context.user_data["placa"]

    await update.message.reply_text("⏳ Consultando dados na FIPE...")
    dados_veiculo = consultar_placa_fipe_api(placa)
    context.user_data["dados_veiculo"] = dados_veiculo
    opcoes = dados_veiculo.get("opcoes", [])

    if not opcoes:
        await update.message.reply_text("❌ Não foi possível encontrar a FIPE deste veículo.")
        return ConversationHandler.END

    # EXCEÇÃO 1: Mais de um código FIPE encontrado
    if len(opcoes) > 1:
        context.user_data["opcoes_fipe"] = opcoes
        teclado = []
        texto_opcoes = "⚠️ *Mais de uma versão FIPE encontrada para este veículo!*\n\nEscolha a versão correta no botão abaixo:\n\n"
        
        for idx, item in enumerate(opcoes):
            val_fmt = f"R${item['valor']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            texto_opcoes += f"*{idx + 1}.* {item['modelo']}\n   ↳ Código: `{item['codigo']}` | Valor: *{val_fmt}*\n\n"
            # Botão de escolha
            teclado.append([
                InlineKeyboardButton(
                    f"{item['modelo'][:28]}... ({val_fmt})",
                    callback_data=f"fipe_{idx}"
                )
            ])
            
        reply_markup = InlineKeyboardMarkup(teclado)
        await update.message.reply_text(texto_opcoes, reply_markup=reply_markup, parse_mode="Markdown")
        return ESCOLHE_FIPE

    # Se só tiver uma FIPE, finaliza direto
    fipe_unica = opcoes[0]
    msg_final = montar_resposta_cotacao(context.user_data, fipe_unica)
    await update.message.reply_text(msg_final, parse_mode="Markdown")
    return ConversationHandler.END

async def callback_escolha_fipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split("_")[1])
    fipe_escolhida = context.user_data["opcoes_fipe"][idx]

    await query.edit_message_reply_markup(reply_markup=None) # Remove os botões
    msg_final = montar_resposta_cotacao(context.user_data, fipe_escolhida)
    await query.message.reply_text(msg_final, parse_mode="Markdown")
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cotação cancelada.")
    return ConversationHandler.END

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), receber_entrada_placa)
        ],
        states={
            RECEBE_NOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_nome)],
            RECEBE_TELEFONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_telefone)],
            ESCOLHE_FIPE: [CallbackQueryHandler(callback_escolha_fipe, pattern=r"^fipe_\d+$")],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)

    print("Bot com suporte a múltipla FIPE rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
