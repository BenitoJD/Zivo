FROM node:22-alpine AS build
WORKDIR /app

ARG NEXT_PUBLIC_API_URL=
ARG NEXT_PUBLIC_AUTH_URL=
ARG NEXT_PUBLIC_STORAGE_URL=
ARG AUTH_PROXY_URL=http://zivo-auth:8000
ARG STORAGE_PROXY_URL=http://zivo-storage:8000
ARG PRACTICE_PROXY_URL=http://zivo-practice:8000
ARG CONTENT_PROXY_URL=http://zivo-content:8000
ARG STUDY_PROXY_URL=http://zivo-study:8000
ARG LIBRARY_PROXY_URL=http://zivo-library:8000
ARG ADMIN_PROXY_URL=http://zivo-admin:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_AUTH_URL=$NEXT_PUBLIC_AUTH_URL
ENV NEXT_PUBLIC_STORAGE_URL=$NEXT_PUBLIC_STORAGE_URL
ENV AUTH_PROXY_URL=$AUTH_PROXY_URL
ENV STORAGE_PROXY_URL=$STORAGE_PROXY_URL
ENV PRACTICE_PROXY_URL=$PRACTICE_PROXY_URL
ENV CONTENT_PROXY_URL=$CONTENT_PROXY_URL
ENV STUDY_PROXY_URL=$STUDY_PROXY_URL
ENV LIBRARY_PROXY_URL=$LIBRARY_PROXY_URL
ENV ADMIN_PROXY_URL=$ADMIN_PROXY_URL

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --prefer-offline
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
ENV AUTH_PROXY_URL=http://zivo-auth:8000
ENV STORAGE_PROXY_URL=http://zivo-storage:8000
ENV PRACTICE_PROXY_URL=http://zivo-practice:8000
ENV CONTENT_PROXY_URL=http://zivo-content:8000
ENV STUDY_PROXY_URL=http://zivo-study:8000
ENV LIBRARY_PROXY_URL=http://zivo-library:8000
ENV ADMIN_PROXY_URL=http://zivo-admin:8000

RUN addgroup -S app && adduser -S app -G app

COPY --from=build --chown=app:app /app/public ./public
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static

USER app
EXPOSE 3000
CMD ["node", "server.js"]
