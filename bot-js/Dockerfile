FROM node:24-bookworm-slim AS runtime
WORKDIR /opt/rallybit/bot-js
COPY bot-js/package.json bot-js/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY bot-js/src ./src
COPY bot-js/contracts ./contracts
COPY docs/command-catalog.json /opt/rallybit/docs/command-catalog.json
ENV NODE_ENV=production RALLYBIT_DATA_DIR=/data API_HOST=0.0.0.0 API_PORT=8080
USER node
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 CMD node -e "fetch('http://127.0.0.1:8080/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "src/index.js"]

FROM runtime AS test
COPY bot-js/test ./test
HEALTHCHECK NONE
CMD ["node", "--test"]
