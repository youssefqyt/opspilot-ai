FROM node:22-alpine
ENV NODE_ENV=production
WORKDIR /srv/report
COPY package*.json ./
RUN npm ci --omit=dev
COPY index.js ./
USER node
EXPOSE 3001
CMD ["node", "index.js"]
