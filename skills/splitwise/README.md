# splitwise — Claude Code skill

Cargá gastos compartidos en Splitwise desde la terminal, en lenguaje natural:

```text
> cargá 48 euros de la cena de anoche, pagué yo, dividido con Ana y Luis
```

Claude resuelve el grupo y las personas contra tu cuenta real, te muestra una vista
previa con quién pagó y quién debe, y recién después de que escribís `CONFIRM` crea el
gasto. Nunca escribe sin confirmación, nunca adivina un nombre ambiguo y avisa si ya
cargaste algo igual ese día.

Sin dependencias: un `SKILL.md` que Claude lee y un script Python de stdlib.

## Contenido

```text
  SKILL.md                      instrucciones que sigue Claude Code
  scripts/splitwise.py          CLI (python3 stdlib, sin pip install)
  references/splitwise-api.md   catálogo condensado de la API v3.0 de Splitwise
```

## 1. Configurar la API de Splitwise

1. Entrá a https://secure.splitwise.com/apps con tu cuenta.
2. **Register your application**. Completá nombre (por ejemplo `claude-splitwise`),
   descripción y una URL cualquiera de homepage (no se usa). Callback URL puede quedar
   vacía: no vamos a usar OAuth.
3. En la página de la app recién creada, bajá hasta **API keys** y tocá **Get API key**.
   Copiá la clave: es un token de acceso completo a tu cuenta, tratala como una
   contraseña.
4. Guardala en un archivo que solo lea tu usuario:

   ```bash
   mkdir -p ~/.config/splitwise
   printf 'SPLITWISE_API_KEY=%s\n' 'PEGAR_LA_CLAVE_ACA' > ~/.config/splitwise/.env
   chmod 600 ~/.config/splitwise/.env
   ```

   Alternativa: exportá `SPLITWISE_API_KEY` en el entorno. El script prueba primero la
   variable y después el archivo. Nunca escribe ese archivo ni imprime la clave.

5. Probá la conexión:

   ```bash
   python3 scripts/splitwise.py me
   ```

   Tiene que devolver tu nombre, email y moneda por defecto. Si ves
   `ERR>  SPLITWISE_API_KEY ausente`, el archivo no está donde el script lo busca.
   Si ves `HTTP 401`, la clave está mal copiada. Para revocarla, generá una nueva
   en la misma página de la app.

Límites que conviene saber: la API self-serve es solo para uso no comercial; devuelve
HTTP 429 si hacés muchas llamadas seguidas (el script reintenta una vez); y bloquea el
User-Agent por defecto de Python con un 403 de Cloudflare, por eso el script manda uno
propio (`splitwise-skill/1.0`).

## 2. Instalar el skill en Claude Code

```bash
mkdir -p ~/.claude/skills
cp -r splitwise ~/.claude/skills/splitwise
python3 ~/.claude/skills/splitwise/scripts/splitwise.py selftest
```

El `selftest` corre los chequeos de aritmética sin tocar la red. Si todo da `OK`,
abrí Claude Code y escribí algo como `balance del grupo <tu grupo>` o `/splitwise`.

Para usarlo en un solo proyecto, copialo a `<repo>/.claude/skills/splitwise` en vez
de `~/.claude/skills`.

## 3. Uso

Lecturas (sin confirmación):

```text
> qué grupos tengo en splitwise
> cuánto me deben en Viaje Grecia
> gastos del grupo Casa desde el 1 de octubre
```

Escrituras (siempre vista previa + `CONFIRM`):

```text
> cargá 48 euros de la cena de anoche, pagué yo, dividido con Ana y Luis
> cargá el alquiler del auto, 300 euros, pagó Luis, 50% yo y 50% Ana
> cargá el super, 62,40, pagué yo 40 y Ana 22,40, repartido en partes iguales
> cambiá el gasto 51023 a 52 euros
> borrá el gasto 51023
```

Reglas de reparto que entiende el script: un solo pagador (`--paid NAME`) o varios con
monto (`--paid NAME=MONTO`); partes iguales entre todo el grupo o entre `--among` los que
nombres; montos exactos (`--share`) o porcentajes (`--percent`). Valida en local que
pagos y deudas sumen el total antes de hacer la llamada.

## 4. Adaptarlo

- **Tu grupo**: nada está hardcodeado. `--group` acepta nombre parcial (sin acentos) o
  id numérico.
- **Moneda**: por defecto la de tu perfil de Splitwise; `--currency EUR` por gasto.
  Recomendación: cargá en la moneda que te cobró la tarjeta, no en la del lugar.
- **Idioma**: los triggers del `description` están en español e inglés; agregá los tuyos.
  Los mensajes del script están en español.
- **Un gasto por evento real**: una cena con propina es una sola entrada; poné el
  desglose en `--details` para que el grupo pueda auditarlo.

## Seguridad

- La clave vive solo en `~/.config/splitwise/.env` o en el entorno.
- Toda escritura pasa por `--dry-run` y una confirmación explícita.
- `delete-expense` pide dos confirmaciones y es un borrado blando (Splitwise permite
  deshacerlo).
- Los duplicados se detectan antes de escribir; `--force` solo después de confirmar.
