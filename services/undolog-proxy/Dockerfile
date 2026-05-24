FROM golang:1.22-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/undolog-proxy ./cmd/proxy

FROM alpine:3.20
RUN adduser -D -H -u 10001 proxy
USER proxy
WORKDIR /app
COPY --from=build /out/undolog-proxy /usr/local/bin/undolog-proxy
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/undolog-proxy"]

