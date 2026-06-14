FROM node:20-alpine AS builder
WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

ARG PUBLIC_API_BASE=https://api.stefhq.io
ENV PUBLIC_API_BASE=$PUBLIC_API_BASE
RUN npm run build

FROM node:20-alpine
WORKDIR /app
COPY --from=builder /app/build ./build

EXPOSE 3000
ENV NODE_ENV=production
CMD ["node", "build"]
