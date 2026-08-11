# Carga los secretos desde `secretos.local` a donde corresponde, sin que
# ningún valor pase por la pantalla, por el historial de un chat, ni por la
# línea de comandos de un proceso.
#
# POR QUÉ EXISTE
# ---------------------------------------------------------------------------
# Los secretos de Rat.IA viven en DOS lugares distintos y es fácil equivocarse:
# los de publicación en redes los lee un workflow de GitHub Actions, y los de
# cobro los lee un Worker de Cloudflare. Un token cargado en el lugar
# equivocado no da error — simplemente el sistema que lo necesita se queda sin
# él y falla en silencio.
#
# Uso:
#     .\cargar-secretos.ps1                # carga todo lo que encuentre
#     .\cargar-secretos.ps1 -SoloVerificar # dice qué haría, sin cargar nada
#
# Después de que corra bien: BORRAR secretos.local. El .gitignore lo cubre,
# pero un archivo con el access token de producción no tiene por qué seguir
# existiendo en el disco.

param(
    [string]$Archivo = "$PSScriptRoot\secretos.local",
    [string]$Repo = "joaquinmunozs/rat.ia",
    [switch]$SoloVerificar
)

$ErrorActionPreference = "Stop"

# Cada secreto va a UN destino. Cambiar esta tabla es cambiar el despliegue,
# así que es lo único que hay que leer para entender el reparto.
$DESTINOS = @{
    # Publicador de Instagram/Facebook -> lo lee .github/workflows/redes.yml
    "META_ACCESS_TOKEN"       = "github"
    "META_PAGE_ID"            = "github"
    "META_IG_USER_ID"         = "github"
    "HF_API_KEY_ID"           = "github"
    "HF_API_KEY_SECRET"       = "github"
    "ANTHROPIC_API_KEY"       = "github"   # lo usa analisis-semanal.yml

    # Worker de cobro -> lo lee cobro/src/index.js
    "MP_ACCESS_TOKEN"         = "worker"
    "ADMIN_TELEGRAM_CHAT_ID"  = "worker"
    "FLOW_API_KEY"            = "worker"
    "FLOW_SECRET_KEY"         = "worker"

    # El token del bot lo necesitan LOS DOS: Héctor manda las alertas desde
    # Actions y el Worker manda la invitación al grupo.
    "TELEGRAM_BOT_TOKEN"      = "ambos"
}

if (-not (Test-Path $Archivo)) {
    Write-Output "No existe $Archivo."
    Write-Output ""
    Write-Output "Créalo con una clave por línea, así:"
    Write-Output "    MP_ACCESS_TOKEN=APP_USR-..."
    Write-Output "    ADMIN_TELEGRAM_CHAT_ID=123456789"
    Write-Output ""
    Write-Output "Nombres que este script sabe repartir:"
    $DESTINOS.Keys | Sort-Object | ForEach-Object { "    $_  ->  $($DESTINOS[$_])" }
    exit 1
}

# Se lee a un diccionario y NUNCA se imprime un valor. Solo nombres y largos:
# el largo alcanza para detectar un pegado a medias sin revelar nada.
$valores = @{}
foreach ($linea in Get-Content $Archivo -Encoding utf8) {
    $t = $linea.Trim()
    if (-not $t -or $t.StartsWith("#")) { continue }
    $corte = $t.IndexOf("=")
    if ($corte -lt 1) {
        Write-Output "  (línea ignorada, no tiene forma NOMBRE=valor)"
        continue
    }
    $nombre = $t.Substring(0, $corte).Trim()
    $valor = $t.Substring($corte + 1).Trim()
    # Comillas de más son el error de pegado más común y dejan el token
    # inválido de una forma que no se nota hasta que la API rechaza.
    $valor = $valor.Trim('"').Trim("'")
    if ($valor) { $valores[$nombre] = $valor }
}

Write-Output "Encontrados $($valores.Count) valor(es) en secretos.local:"
foreach ($n in ($valores.Keys | Sort-Object)) {
    $destino = if ($DESTINOS.ContainsKey($n)) { $DESTINOS[$n] } else { "DESCONOCIDO" }
    Write-Output ("  {0,-24} {1,4} caracteres  ->  {2}" -f $n, $valores[$n].Length, $destino)
}
Write-Output ""

$desconocidos = $valores.Keys | Where-Object { -not $DESTINOS.ContainsKey($_) }
if ($desconocidos) {
    Write-Output "OJO: estos nombres no los conozco y no se van a cargar en ninguna parte:"
    $desconocidos | ForEach-Object { Write-Output "  $_" }
    Write-Output "Revisa que estén bien escritos (mayúsculas incluidas)."
    Write-Output ""
}

if ($SoloVerificar) {
    Write-Output "Modo verificación: no se cargó nada."
    exit 0
}

# ── GitHub ───────────────────────────────────────────────────────────────
# El valor va por STDIN, no como argumento: un `--body $valor` queda visible
# en la lista de procesos mientras el comando corre.
$aGithub = $valores.Keys | Where-Object { $DESTINOS[$_] -in @("github", "ambos") }
if ($aGithub) {
    Write-Output "── GitHub Actions ($Repo) ──"
    foreach ($n in ($aGithub | Sort-Object)) {
        $valores[$n] | gh secret set $n --repo $Repo
        if ($LASTEXITCODE -eq 0) {
            Write-Output "  ok      $n"
        } else {
            Write-Output "  FALLÓ   $n  (¿tienes permiso admin en el repo?)"
        }
    }
    Write-Output ""
}

# ── Cloudflare Worker ────────────────────────────────────────────────────
$aWorker = $valores.Keys | Where-Object { $DESTINOS[$_] -in @("worker", "ambos") }
if ($aWorker) {
    Write-Output "── Worker de cobro (Cloudflare) ──"
    Push-Location "$PSScriptRoot\cobro"
    try {
        foreach ($n in ($aWorker | Sort-Object)) {
            $valores[$n] | npx wrangler secret put $n
            if ($LASTEXITCODE -eq 0) {
                Write-Output "  ok      $n"
            } else {
                Write-Output "  FALLÓ   $n  (¿corriste 'npx wrangler login'?)"
            }
        }
    } finally {
        Pop-Location
    }
    Write-Output ""
}

Write-Output "Listo. Ahora borra el archivo:"
Write-Output "    Remove-Item '$Archivo'"
