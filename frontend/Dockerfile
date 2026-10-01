# 前端镜像：多阶段构建，产出静态资源由 Nginx 提供，API 反向代理到后端容器
FROM node:20-alpine AS builder

WORKDIR /app

# 依赖层缓存
COPY package.json package-lock.json* ./
# 优先用 npm ci（严格按 lock 安装）；lock 与 package.json 不一致时回退 npm install
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY . .

ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build


FROM nginx:1.27-alpine AS runner

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=20s --timeout=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
