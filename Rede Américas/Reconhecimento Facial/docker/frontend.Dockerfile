FROM nginx:alpine

# Contexto de build = Dev/Poc/ (raiz do design system)
# Isso permite incluir assets/ compartilhados entre todos os clientes

COPY ["Rede Américas/Reconhecimento Facial/docker/nginx.conf", "/etc/nginx/conf.d/default.conf"]

# Assets globais do design system
COPY assets/ /usr/share/nginx/html/assets/

# Telas do frontend
COPY ["Rede Américas/Reconhecimento Facial/frontend/login.html",               "/usr/share/nginx/html/"]
COPY ["Rede Américas/Reconhecimento Facial/frontend/cadastro-identidade.html", "/usr/share/nginx/html/"]
COPY ["Rede Américas/Reconhecimento Facial/frontend/resultado.html",           "/usr/share/nginx/html/"]

# Páginas de erro customizadas
COPY ["Rede Américas/Reconhecimento Facial/frontend/404.html", "/usr/share/nginx/html/"]
COPY ["Rede Américas/Reconhecimento Facial/frontend/50x.html", "/usr/share/nginx/html/"]

EXPOSE 80
