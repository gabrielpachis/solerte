import os
# ===================================================================
# 🤖 CONFIGURAÇÕES DO TELEGRAM
# ===================================================================
# Token do seu bot do Telegram, obtido com o @BotFather.
TOKEN_BOT = "8478570405:AAGeg9JP3NUYfS4mnSF9oZMg45w8YIZavpE" # Mantenha o seu token

# Lista de IDs de usuários do Telegram que são donos/administradores do bot.
# Eles receberão notificações de novas vendas. Pode ser um ou mais IDs.
# Exemplo: ID_DONOS = [123456789, 987654321]
ID_DONOS = [7885440781] # Mantenha o seu ID

# ID do canal para onde os logs de execução serão enviados.
ID_LOGS = 6912464825 # Mantenha o seu ID de logs

# ID do seu canal privado/exclusivo onde os membros serão adicionados.
ID_CANAL_PRIVADO = -1003060363521 # Mantenha o ID do seu canal privado

# ID do canal onde o aceite dos termos será registrado.
ID_CANAL_TERMOS = -1003096889643 # Mantenha o ID do seu canal de termos

# ===================================================================
# 💳 CONFIGURAÇÕES DOS PLANOS
# ===================================================================
# Preços dos planos de acesso. Use formato de string com ponto.
PRECO_MENSAL = "39.90"
PRECO_TRIMESTRAL = "74.90" # Defina o preço para o acesso trimestral

LINK_SUPORTE = "https://t.me/paleoselli"

# ===================================================================
# 🏦 CONFIGURAÇÕES DA EFÍ BANK
# ===================================================================
# Suas credenciais da API da Efí. Você as encontra no painel da Efí,
# na seção API -> Aplicações.
EFI_CLIENT_ID = "Client_Id_bb18900094272c019c0f27731259a8c75e423853"
EFI_CLIENT_SECRET = "Client_Secret_1fc16f6c319cdaba2f4864efcf8191983ff53034"

# Define se o ambiente é de produção (True) ou de homologação/sandbox (False).
# Use False para testar, e mude para True quando for para produção.
EFI_PRODUCAO = True 

# Chave PIX que será usada para gerar as cobranças.
# IMPORTANTE: Esta chave deve estar cadastrada na conta Efí correspondente
# às credenciais acima.
EFI_PIX_KEY = "programadorpaleoselli@gmail.com" # Mantenha sua chave PIX

# Caminho para o arquivo de certificado .pem da Efí para o ambiente de PRODUÇÃO.
DIRETORIO_ATUAL = os.path.dirname(__file__)
EFI_CERTIFICATE_PATH = os.path.join(DIRETORIO_ATUAL, 'certificados', 'certificado_producao.pem')







