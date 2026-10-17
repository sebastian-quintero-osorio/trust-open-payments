FROM golang:1.26.9-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -buildid=" -o /out/op-service ./cmd/op-service

FROM gcr.io/distroless/static-debian13:nonroot
COPY --from=build --chown=65532:65532 /out/op-service /op-service
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD ["/op-service", "healthcheck"]
ENTRYPOINT ["/op-service"]
