# 15 · Lista de espera

Estado: ☐ pendiente · Fase: 3 · Dueño: — · Carpeta: `app/modules/waitlist/`

## Objetivo
Recibir lo que ya envía el formulario de la landing (`landing/src/components/waitlist/WaitlistForm.astro`), con y sin JavaScript, sin errores raros para quien se inscribe dos veces.

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
| ★ | `POST /api/v1/waitlist` | público | JSON o `application/x-www-form-urlencoded`: `{tipo: comercio\|prestamista, nombre, correo, ciudad, website?}` | JSON: 201 `{"status": "created"}` (nuevo) · 200 `{"status": "exists"}` (ya estaba). Form: 303 a la landing | 422 `validation_error` (solo JSON), 429 `rate_limited` |

## Reglas
- **R1. Campos** (los del formulario): `tipo` ∈ `waitlist_tipo`; `nombre` 1–120, `ciudad` 1–80, ambos con `strip()`; `correo` email válido ≤ 254, guardado en minúsculas; `website` es el honeypot y no se guarda.
- **R2. Formatos**: con JS la landing manda JSON (`Content-Type: application/json`, `Accept: application/json`); sin JS el navegador manda urlencoded y navega a la respuesta. Se distinguen por `Content-Type`; el urlencoded se lee con `urllib.parse.parse_qs` sobre el body (sin agregar `python-multipart`) y los dos pasan por el mismo modelo Pydantic.
- **R3. Upsert** por `lower(correo)`: si no existe → inserta y 201; si existe → actualiza `tipo`, `nombre`, `ciudad` y 200. **Nunca 409.** Dos peticiones simultáneas con el mismo correo: el que choca con el único reintenta como actualización.
- **R4. Honeypot**: `website` no vacío → responde como éxito (201 JSON o 303 form) sin guardar nada.
- **R5. Respuesta del form**: 303 a `{LANDING_URL}/?lista=ok#lista-de-espera`; datos inválidos → 303 a `{LANDING_URL}/?lista=error#lista-de-espera` (el navegador ya valida `required` y `type=email`).
- **R6. CORS**: el origen de la landing está en `CORS_ORIGINS` (middleware de `00-core`); el preflight `OPTIONS` permite `POST` con `Content-Type` y `Accept`.
- **R7. Rate limit** en memoria por IP: máximo 10 peticiones por ventana de 60 s; la 11.ª → 429 `rate_limited` (también en el form). IP = primer valor de `X-Forwarded-For` (Caddy) o `request.client.host`. El limitador expone `reset()` para los tests.
- **R8.** No requiere token; no crea usuarios ni notificaciones.

## Datos
- Escribe: `waitlist` (definida en `01-data-model.md`).

## Depende de
- `00-core` (CORS, formato de errores).

## Criterios de aceptación
- [ ] **AC-1.** `POST /waitlist` JSON `{tipo: "comercio", nombre: "Marta Rojas", correo: "Marta@Ejemplo.com", ciudad: "Bucaramanga"}` → 201 y la fila guarda `correo "marta@ejemplo.com"` (→ `test_ac1_waitlist_created`).
- [ ] **AC-2.** Repetir con `correo "MARTA@ejemplo.com"` y `nombre "Marta R."` → 200 `{"status": "exists"}`, sigue habiendo 1 fila y `nombre == "Marta R."` (→ `test_ac2_waitlist_upsert_no_409`).
- [ ] **AC-3.** `tipo "banco"` o `correo "marta@"` → 422 `validation_error` y sin filas (→ `test_ac3_waitlist_validation`).
- [ ] **AC-4.** El mismo envío como urlencoded → 303 con `Location` = `{LANDING_URL}/?lista=ok#lista-de-espera` y la fila guardada (→ `test_ac4_waitlist_form_post`).
- [ ] **AC-5.** Con `website: "http://spam"` → 201 y sin filas (→ `test_ac5_waitlist_honeypot`).
- [ ] **AC-6.** 10 envíos desde la misma IP pasan y el 11.º en menos de 60 s → 429 `rate_limited`; desde otra IP (`X-Forwarded-For`) → 201 (→ `test_ac6_waitlist_rate_limit`).
- [ ] **AC-7.** Preflight `OPTIONS /api/v1/waitlist` con `Origin` de la landing (en `CORS_ORIGINS`) → `Access-Control-Allow-Origin` igual a ese origen (→ `test_ac7_waitlist_cors`).

## Fuera de alcance
- Confirmación por correo, doble opt-in, exportar la lista y captcha.
- Rate limit compartido entre procesos (hay uno solo).
