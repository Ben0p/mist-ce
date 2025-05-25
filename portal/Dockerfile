FROM node:20-alpine

RUN apk add --update --no-cache git nginx

WORKDIR /portal
COPY ./ ./

RUN npm update && npm install -g -U --no-optional @web/dev-server rollup --unsafe-perm && npm install && npm run build && cp package.json /

COPY ./container/nginx.conf /etc/nginx/nginx.conf
COPY ./container/entry.sh /entry.sh

EXPOSE 80 8000

ENTRYPOINT [ "/entry.sh" ]
