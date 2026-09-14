FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
# NEXT_PUBLIC_* vars are inlined into the client bundle at build time, not read at
# runtime - must be passed as a build arg, a plain `environment:` in compose won't work.
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
