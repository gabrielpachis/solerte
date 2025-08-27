# --- Requisitos ---
# pip install python-telegram-bot efipay pytz

import asyncio
import sys
import logging
import sqlite3
import re
import html
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, constants
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, PicklePersistence, MessageHandler, filters
from telegram.error import BadRequest
from efipay import EfiPay
# Você precisa ter um arquivo senhas.py com suas credenciais
from senhas import (
    TOKEN_BOT,
    EFI_CLIENT_ID, EFI_CLIENT_SECRET, EFI_PRODUCAO, EFI_PIX_KEY,
    EFI_CERTIFICATE_PATH,
    ID_DONOS,
    PRECO_MENSAL, PRECO_TRIMESTRAL, # Alterado de VITALICIO para TRIMESTRAL
    ID_CANAL_PRIVADO,
    ID_CANAL_TERMOS,
    ID_LOGS,
    LINK_SUPORTE
)

# -----------------------------------------------------------------------------
# 🤖 CLASSE DE LOGGING PARA O TELEGRAM
# -----------------------------------------------------------------------------
class TelegramLogHandler(logging.Handler):
    """
    Um handler de logging que envia registros para um chat do Telegram.
    """
    def __init__(self, bot: Bot, chat_id: int):
        super().__init__()
        self.bot = bot
        self.chat_id = chat_id

    def emit(self, record):
        """Formata, escapa e envia o registro de log."""
        log_entry = self.format(record)
        escaped_log_entry = html.escape(log_entry)

        if len(escaped_log_entry) > 4000:
            escaped_log_entry = escaped_log_entry[:4000] + "\n\n[LOG TRUNCADO POR SER MUITO LONGO]"

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.bot.send_message(
                chat_id=self.chat_id,
                text=f"<pre>{escaped_log_entry}</pre>",
                parse_mode=constants.ParseMode.HTML
            ))
        except (RuntimeError, Exception) as e:
            print(f"ERRO INESPERADO no handler de log do Telegram: {e}")


# -----------------------------------------------------------------------------
# ⚙️ CONFIGURAÇÕES INICIAIS
# -----------------------------------------------------------------------------
try:
    # Inicializa a API da Efí com as credenciais fornecidas
    credentials = {
        'client_id': EFI_CLIENT_ID,
        'client_secret': EFI_CLIENT_SECRET,
        'sandbox': not EFI_PRODUCAO,
        'certificate': EFI_CERTIFICATE_PATH
    }
    efi = EfiPay(credentials)
except Exception as e:
    logging.critical(f"Falha CRÍTICA ao inicializar a API da Efí. Erro: {e}")
    sys.exit("Erro fatal na inicialização da Efí.")

DB_FILE = 'pagamentos.db'
logger = logging.getLogger(__name__)

# --- CONSTANTES PARA OS TERMOS DE USO ---
TERMS_URL = "https://docs.google.com/document/d/10l_slgZHCnQw4tSjARx52VU8wNYEoU3qvqLCcTpmB1A/edit?usp=sharing" # Mantenha o seu


# -----------------------------------------------------------------------------
# 🗂️ FUNÇÕES DO BANCO DE DADOS
# -----------------------------------------------------------------------------
def criar_e_migrar_db():
    """Cria e configura o banco de dados, adicionando colunas se não existirem."""
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pagamentos (
                txid TEXT PRIMARY KEY, user_id INTEGER NOT NULL, username TEXT NOT NULL,
                status TEXT NOT NULL, data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_aprovacao TIMESTAMP,
                pix_message_id INTEGER
            )
        ''')
        
        # --- MIGRAÇÃO: Adiciona novas colunas se não existirem ---
        cursor.execute("PRAGMA table_info(pagamentos)")
        columns = [info[1] for info in cursor.fetchall()]
        
        if 'tipo_plano' not in columns:
            cursor.execute("ALTER TABLE pagamentos ADD COLUMN tipo_plano TEXT NOT NULL DEFAULT 'mensal'")
            logger.info("Coluna 'tipo_plano' adicionada ao banco de dados.")
            
        if 'valor' not in columns:
            cursor.execute("ALTER TABLE pagamentos ADD COLUMN valor REAL NOT NULL DEFAULT 0.0")
            logger.info("Coluna 'valor' adicionada ao banco de dados.")

        conn.commit()
        logger.info("Banco de dados verificado e pronto para uso.")
    except sqlite3.Error as e:
        logger.critical(f"Erro CRÍTICO no banco de dados: {e}")
        sys.exit(f"Falha ao inicializar o banco de dados: {e}")
    finally:
        if conn:
            conn.close()

# -----------------------------------------------------------------------------
# 💳 FUNÇÃO DE PAGAMENTO
# -----------------------------------------------------------------------------
def criar_pagamento_efi(valor: float, user_id: int, tipo_plano: str):
    """Cria uma cobrança PIX na Efí e retorna o txid e o código Copia e Cola."""
    try:
        body = {
            "calendario": {"expiracao": 900},  # 15 minutos
            "valor": {"original": f"{valor:.2f}"},
            "chave": EFI_PIX_KEY,
            "solicitacaoPagador": f"Acesso {tipo_plano} para user ID {user_id}"
        }
        response_charge = efi.pix_create_immediate_charge(body=body)
        txid = response_charge.get('txid')
        loc_id = response_charge.get('loc', {}).get('id')
        if not txid or not loc_id:
            raise ValueError(f"API não retornou 'txid' ou 'loc.id': {response_charge}")

        response_qrcode = efi.pix_generate_qrcode(params={'id': loc_id})
        pix_copia_cola = response_qrcode.get('pixCopiaECola') or response_qrcode.get('qrcode')
        if not pix_copia_cola:
            raise ValueError(f"'pixCopiaECola' ou 'qrcode' não encontrados: {response_qrcode}")
            
        logger.info(f"Cobrança {txid} (Plano: {tipo_plano}, Valor: {valor}) criada para user {user_id}.")
        return {"txid": txid, "pixCopiaECola": pix_copia_cola}

    except Exception as e:
        logger.error(f"Erro CRÍTICO na API Efí ao criar cobrança para {user_id}: {e}", exc_info=True)
        return None

# -----------------------------------------------------------------------------
# 🤖 HANDLERS DE COMANDOS E CALLBACKS DO TELEGRAM
# -----------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para o comando /start e para o botão 'Voltar' ao menu principal."""
    user = update.effective_user
    if not user: return
    logger.info(f"Usuário {user.id} ({user.first_name}) iniciou o bot ou voltou ao menu.")

    # --- TEXTO DE BOAS-VINDAS ATUALIZADO ---
    texto_boas_vindas = (
        f"Oi, {user.first_name}! 👋\n\n"
        "Você tá entrando num espaço feito só pra quem curte exclusividade.\n\n"
        "Aqui eu compartilho conteúdo que não vai pra lugar nenhum além desse canal.\n\n"
        "Clica aí e vem fazer parte disso."
    )
    
    keyboard = [
        [InlineKeyboardButton("✨ Quero Acesso Exclusivo", callback_data="mostrar_planos")],
        [InlineKeyboardButton("📞 Preciso de Ajuda", url=LINK_SUPORTE)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Limpa dados de usuário para garantir um fluxo novo e registra a atividade
    context.user_data.clear()
    context.user_data['last_activity_time'] = datetime.now()

    if update.message:
        await update.message.reply_text(texto_boas_vindas, reply_markup=reply_markup)
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(texto_boas_vindas, reply_markup=reply_markup)
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                logger.warning(f"Erro ao editar mensagem para /start: {e}")

async def mostrar_planos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra os planos disponíveis, apagando a mensagem PIX anterior se existir."""
    query = update.callback_query
    if not query or not query.from_user: return
    user_id = query.from_user.id
    context.user_data['last_activity_time'] = datetime.now() # Registra atividade
    await query.answer()

    if context.user_data.get('pix_message_id'):
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['pix_message_id'])
            logger.info(f"Mensagem de PIX ({context.user_data['pix_message_id']}) apagada para o usuário {user_id} ao voltar para os planos.")
        except BadRequest as e:
            if "Message to delete not found" not in str(e):
                logger.warning(f"Não foi possível apagar a mensagem de PIX para {user_id}: {e}")
        finally:
            context.user_data['pix_message_id'] = None

    # --- TEXTO DOS PLANOS MODIFICADO ---
    texto = (
        "✨ *Acesso ao Conteúdo Exclusivo*\n\n"
        "Para ter acesso a todo o conteúdo, escolha um dos planos abaixo e faça parte do nosso canal privado:"
    )
    keyboard = [
        [InlineKeyboardButton(f"🌙 Acesso Mensal - R$ {PRECO_MENSAL}", callback_data="plano_mensal")],
        [InlineKeyboardButton(f"🌟 Acesso Trimestral - R$ {PRECO_TRIMESTRAL}", callback_data="plano_trimestral")], # Alterado
        [InlineKeyboardButton("⬅️ Voltar", callback_data="start")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(texto, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Erro ao mostrar planos: {e}")

async def mostrar_termos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pega o plano escolhido, salva e exibe os termos."""
    query = update.callback_query
    if not query: return
    context.user_data['last_activity_time'] = datetime.now() # Registra atividade
    await query.answer()

    plano_selecionado = query.data.split('_')[1]  

    if plano_selecionado == 'mensal':
        context.user_data['plano_escolhido'] = {'tipo': 'mensal', 'valor': PRECO_MENSAL}
    elif plano_selecionado == 'trimestral': # Alterado de 'vitalicio'
        context.user_data['plano_escolhido'] = {'tipo': 'trimestral', 'valor': PRECO_TRIMESTRAL} # Alterado
    else:
        logger.error(f"Plano inválido '{plano_selecionado}' recebido do usuário {query.from_user.id}")
        await query.edit_message_text("❌ Erro: Plano inválido. Por favor, tente novamente.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="mostrar_planos")]]))
        return

    logger.info(f"Usuário {query.from_user.id} escolheu o plano: {plano_selecionado}")

    # --- TEXTO DOS TERMOS MODIFICADO ---
    texto = (
        "⚠️ *Quase lá... Leia os Termos*\n\n"
        "Antes de prosseguir, é importante que você leia e concorde com os nossos termos de uso. "
        "Isso garante que tudo fique claro entre a gente.\n\n"
        "Ao clicar em 'Aceito', você confirma que leu e está de acordo."
    )
    keyboard = [
        [InlineKeyboardButton("Ler Termos de Uso", url=TERMS_URL)],
        [InlineKeyboardButton("✅ Li e aceito os Termos", callback_data="aceitar_termos")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="mostrar_planos")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(texto, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN)


async def aceitar_termos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa o aceite dos termos e inicia o fluxo de pagamento."""
    query = update.callback_query
    if not query or not query.from_user: return
    user = query.from_user
    context.user_data['last_activity_time'] = datetime.now() # Registra atividade

    try:
        await query.answer()
    except BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning(f"Query antiga recebida do usuário {user.id}. Enviando nova mensagem.")
            await context.bot.send_message(
                chat_id=user.id,
                text="Sua sessão expirou. Por favor, inicie o processo novamente.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ir para o início", callback_data="start")]])
            )
            return
        else:
            logger.error(f"Erro inesperado ao responder callback em aceitar_termos: {e}")
            raise

    if 'plano_escolhido' not in context.user_data:
        logger.warning(f"Usuário {user.id} chegou em 'aceitar_termos' sem plano escolhido. Redirecionando.")
        await query.edit_message_text(
            text="❗️Opa! Parece que sua sessão foi reiniciada. Por favor, escolha um plano novamente.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Escolher Plano", callback_data="mostrar_planos")]])
        )
        return

    await query.edit_message_text("Termos aceitos! Preparando seu acesso...")
    
    brasilia_tz = pytz.timezone('America/Sao_Paulo')
    now_brasilia = datetime.now(brasilia_tz)
    data_hora_brasilia = now_brasilia.strftime('%d/%m/%Y às %H:%M:%S')
    username = f"@{user.username}" if user.username else "N/A"
    plano_info = context.user_data['plano_escolhido']
    
    user_info = (
        f"✅ *Termo de Uso Aceito*\n\n"
        f"👤 *Usuário:* {user.full_name} ({username})\n"
        f"🆔 *Chat ID:* `{user.id}`\n"
        f"💎 *Plano Escolhido:* {plano_info['tipo'].capitalize()} (R$ {plano_info['valor']})\n"
        f"🗓️ *Data e Hora (Brasília):* {data_hora_brasilia}"
    )
    try:
        await context.bot.send_message(chat_id=ID_CANAL_TERMOS, text=user_info, parse_mode=constants.ParseMode.MARKDOWN)
        logger.info(f"Usuário {user.id} aceitou os termos para o plano {plano_info['tipo']}.")
    except Exception as e:
        logger.error(f"Falha ao enviar notificação de aceite de termos para {ID_CANAL_TERMOS}: {e}")
        
    await gerar_pagamento(update, context)


async def gerar_pagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gera a cobrança PIX com base no plano salvo em user_data."""
    query = update.callback_query
    if not query or not query.from_user: return
    user_id = query.from_user.id
    context.user_data['last_activity_time'] = datetime.now() # Registra atividade
    
    plano = context.user_data.get('plano_escolhido')
    if not plano:
        logger.warning(f"Usuário {user_id} tentou gerar pagamento sem plano escolhido.")
        await query.edit_message_text(
            "❗️Opa! Parece que você não escolheu um plano. Vamos voltar.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Escolher Plano", callback_data="mostrar_planos")]])
        )
        return

    await query.edit_message_text("⏳ Preparando seu pagamento... um instante.")
    
    valor_plano = float(plano['valor'])
    tipo_plano = plano['tipo']
    
    pagamento_info = criar_pagamento_efi(valor_plano, user_id, tipo_plano)

    if pagamento_info and pagamento_info.get("txid") and pagamento_info.get("pixCopiaECola"):
        txid_gerado = pagamento_info["txid"]
        pix_copia_cola = pagamento_info["pixCopiaECola"]
        username = query.from_user.username or f"id_{user_id}"
        
        valor_plano_str = f"{valor_plano:.2f}".replace('.', ',')
        
        # --- TEXTO DE PAGAMENTO MODIFICADO ---
        texto_instrucoes = (
            f"🔑 *Seu Acesso ao Canal*\n\n"
            f"Plano: *{tipo_plano.capitalize()}*\nValor: *R$ {valor_plano_str}*.\n\n"
            f"Para finalizar, use o *PIX Copia e Cola* abaixo. Seu acesso será liberado automaticamente assim que o pagamento for confirmado.\n\n"
            f"Após pagar, clique em *'Já paguei'* para fazer a verificação."
        )

        await query.edit_message_text(
            text=texto_instrucoes,
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Já paguei, verificar acesso", callback_data="verificar")]])
        )
        
        if context.user_data.get('pix_message_id'):
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['pix_message_id'])
            except BadRequest:
                pass
        
        pix_copia_cola_escaped = html.escape(pix_copia_cola)
        pix_message = await context.bot.send_message(
            chat_id=user_id,
            text=f"<pre><code>{pix_copia_cola_escaped}</code></pre>",
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True
        )
        context.user_data['pix_message_id'] = pix_message.message_id
        
        conn = sqlite3.connect(DB_FILE, timeout=10)
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE pagamentos SET status = ? WHERE user_id = ? AND status = ?", ('cancelada', user_id, 'pendente'))
            cursor.execute(
                'INSERT INTO pagamentos (txid, user_id, username, status, pix_message_id, tipo_plano, valor) VALUES (?, ?, ?, ?, ?, ?, ?)',  
                (txid_gerado, user_id, username, "pendente", pix_message.message_id, tipo_plano, valor_plano)
            )
            conn.commit()
            logger.info(f"Cobrança {txid_gerado} (msg: {pix_message.message_id}, plano: {tipo_plano}) salva no DB para {user_id}.")
        finally:
            conn.close()
    else:
        logger.error(f"Falha crítica ao gerar cobrança Efí para {user_id}.")
        await query.edit_message_text("❌ Algo deu errado ao gerar o pagamento. Por favor, contate o suporte.")

async def verificar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica o status, e concede acesso conforme o plano."""
    query = update.callback_query
    if not query or not query.from_user: return
    user_id = query.from_user.id
    context.user_data['last_activity_time'] = datetime.now() # Registra atividade

    await query.answer("Verificando seu pagamento, um momento...")

    txid = None
    tipo_plano_db = None
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT txid, tipo_plano FROM pagamentos WHERE user_id = ? AND status = 'pendente' ORDER BY data_criacao DESC LIMIT 1", (user_id,))
        resultado_db = cursor.fetchone()
        if resultado_db:
            txid, tipo_plano_db = resultado_db
    finally:
        conn.close()

    if not txid:
        await query.edit_message_text(
            "❌ Nenhuma cobrança ativa foi encontrada. Clique abaixo para gerar uma.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Escolher Plano de Acesso", callback_data="mostrar_planos")]])
        )
        return

    try:
        resultado_api = efi.pix_detail_charge(params={'txid': txid})
        status_api = resultado_api.get('status')

        if status_api == 'CONCLUIDA':
            conn = sqlite3.connect(DB_FILE, timeout=10)
            try:
                cursor = conn.cursor()
                cursor.execute("UPDATE pagamentos SET status = ?, data_aprovacao = ? WHERE txid = ?", ('aprovado', datetime.now().isoformat(), txid))
                conn.commit()
            finally:
                conn.close()

            logger.info(f"PAGAMENTO APROVADO! Plano: {tipo_plano_db}, txid {txid} para usuário {user_id}.")

            if context.user_data.get('pix_message_id'):
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['pix_message_id'])
                except BadRequest: pass
                finally: context.user_data.clear()
            
            try:
                # --- TEXTOS DE SUCESSO MODIFICADOS ---
                if tipo_plano_db == 'trimestral':
                    texto_sucesso = (
                        "<b>Acesso Trimestral Liberado!</b>\n\n"
                        "Seu acesso de 93 dias está ativo. Aproveite todo o conteúdo exclusivo."
                    )
                else:  # Plano Mensal
                    texto_sucesso = (
                        "<b>Acesso Mensal Liberado!</b>\n\n"
                        "Seu acesso de 31 dias está ativo. Aproveite todo o conteúdo exclusivo."
                    )
                
                # A data de expiração precisa ser um Unix Timestamp (inteiro).
                expire_time = datetime.now() + timedelta(hours=1)
                expire_timestamp = int(expire_time.timestamp())

                link = await context.bot.create_chat_invite_link(
                    chat_id=ID_CANAL_PRIVADO,  
                    member_limit=1,
                    expire_date=expire_timestamp # Link expira em 1 hora
                )
                
                safe_link = html.escape(link.invite_link)
                
                texto_final = (
                    f"✅ Pagamento confirmado!\n\n{texto_sucesso}\n\n"
                    f'Clique no link abaixo para entrar:\n<a href="{safe_link}">{safe_link}</a>\n\n'
                    f"<i>Atenção: O link é de uso único e pessoal. Ele expira em breve.</i>"
                )

                await query.edit_message_text(
                    text=texto_final,
                    parse_mode=constants.ParseMode.HTML,
                    disable_web_page_preview=True
                )
            
            except Exception as e:
                logger.error(f"PAGAMENTO APROVADO, MAS FALHOU AO GERAR/ENVIAR LINK para {user_id} ({txid}): {e}", exc_info=True)
                
                texto_erro_html = (
                    f"✅ Pagamento aprovado, mas tive um problema ao gerar seu link!\n\n"
                    f"<b>Não se preocupe!</b> Contate o suporte informando seu ID (<code>{user_id}</code>) para receber o acesso."
                )
                await query.edit_message_text(
                    text=texto_erro_html,
                    parse_mode=constants.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Falar com Suporte", url=LINK_SUPORTE)]])
                )
                return 

            try:
                user = query.from_user
                user_details = f"{user.first_name or ''} (@{user.username or 'N/A'}, ID: {user.id})"
                
                # --- LÓGICA DE NOTIFICAÇÃO PARA ADMIN MODIFICADA ---
                dias_expiracao = 0
                if tipo_plano_db == 'trimestral':
                    dias_expiracao = 93
                elif tipo_plano_db == 'mensal':
                    dias_expiracao = 31

                data_expiracao_acesso = datetime.now() + timedelta(days=dias_expiracao)
                brasilia_tz = pytz.timezone('America/Sao_Paulo')
                expire_time_br = data_expiracao_acesso.astimezone(brasilia_tz)
                data_expiracao_formatada = expire_time_br.strftime('%d/%m/%Y às %H:%M')
                
                texto_notificacao_admin = (
                    f"🎉 Novo acesso <b>{tipo_plano_db.upper()}</b> liberado!\n\n"
                    f"Usuário: {html.escape(user_details)}\n"
                    f"🗓️ <b>Expira em:</b> {data_expiracao_formatada} (Horário de Brasília)"
                )
                
                for admin_id in ID_DONOS:
                    await context.bot.send_message(
                        chat_id=admin_id,  
                        text=texto_notificacao_admin,
                        parse_mode=constants.ParseMode.HTML
                    )
            except Exception as e:
                logger.error(f"Sucesso ao liberar acesso para {user_id}, mas falha ao notificar admins: {e}", exc_info=True)

        else:
            safe_status_api = html.escape(status_api)
            # --- TEXTO DE ESPERA MODIFICADO ---
            texto_espera = (f"❌ Pagamento Pendente (status: <b>{safe_status_api}</b>).\n\n"
                            "Se você já pagou, pode levar alguns minutos para o sistema confirmar. "
                            "Aguarde um pouco e tente verificar novamente.")
            
            keyboard_falha = InlineKeyboardMarkup([
                [InlineKeyboardButton("Tentar novamente", callback_data="verificar")],
                [InlineKeyboardButton("⬅️ Escolher outro plano", callback_data="mostrar_planos")]
            ])
            try:
                await query.edit_message_text(text=texto_espera, reply_markup=keyboard_falha, parse_mode=constants.ParseMode.HTML)
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    await query.answer("O status do pagamento ainda não mudou. Por favor, aguarde.", show_alert=True)
                else:
                    raise 

    except Exception as e:
        logger.error(f"Erro geral em 'verificar' para {user_id}: {e}", exc_info=True)
        try:
            await query.edit_message_text("❌ Ocorreu um erro interno. A equipe de suporte já foi notificada.")
        except BadRequest:  
            await query.answer("❌ Ocorreu um erro interno.", show_alert=True)

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reinicia o bot se o usuário estiver inativo ou sem um processo em andamento."""
    if not update.message or not update.message.from_user:
        return

    user_id = update.message.from_user.id
    last_activity = context.user_data.get('last_activity_time')

    if not last_activity or (datetime.now() - last_activity > timedelta(minutes=10)):
        logger.info(f"Usuário inativo {user_id} enviou uma mensagem. Reiniciando o fluxo.")
        await start(update, context)
    else:
        logger.info(f"Usuário ativo {user_id} enviou uma mensagem de texto no meio do fluxo. Ignorando.")


# -----------------------------------------------------------------------------
# 🚀 FUNÇÃO PRINCIPAL E INICIALIZAÇÃO DO BOT
# -----------------------------------------------------------------------------
def main():
    """Função principal que configura e executa o bot."""
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
    logging.getLogger("efipay").setLevel(logging.INFO)

    persistence = PicklePersistence(filepath="bot_persistence")
    app = Application.builder().token(TOKEN_BOT).persistence(persistence).build()
    
    # Handlers de comando e callback
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(start, pattern="^start$"))
    app.add_handler(CallbackQueryHandler(mostrar_planos, pattern="^mostrar_planos$"))
    app.add_handler(CallbackQueryHandler(mostrar_termos, pattern=r"^plano_"))  
    app.add_handler(CallbackQueryHandler(aceitar_termos, pattern="^aceitar_termos$"))
    app.add_handler(CallbackQueryHandler(verificar, pattern="^verificar$"))

    # Handler para qualquer mensagem de texto (baixa prioridade)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_any_message))

    # Adiciona o handler de logs para o Telegram
    telegram_handler = TelegramLogHandler(bot=app.bot, chat_id=ID_LOGS)
    telegram_handler.setFormatter(logging.Formatter('LEVEL: %(levelname)s\nFILE: %(name)s\nMESSAGE: %(message)s'))
    logging.getLogger().addHandler(telegram_handler)

    app.run_polling()

if __name__ == "__main__":
    criar_e_migrar_db()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
