#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Ставит tuigler в систему — одинаково на Omarchy, Arch и Debian-based:
# копирует программу, ярлык для лаунчера и иконку. На Omarchy ещё добавляет
# пункт в меню и предлагает сделать окно непрозрачным.
#
#   ./install.sh              в /usr/local (спросит sudo)
#   ./install.sh --user       в ~/.local, без sudo
#   ./install.sh --opaque     непрозрачное окно, не спрашивая (--no-opaque — убрать)
#   ./install.sh --uninstall  удалить (вместе с --user — из ~/.local)
set -euo pipefail
cd "$(dirname "$0")"

PREFIX=/usr/local
SUDO=sudo
ACTION=install
OPAQUE=ask
for arg in "$@"; do
  case $arg in
    --user) PREFIX=$HOME/.local SUDO= ;;
    --uninstall) ACTION=uninstall ;;
    --opaque) OPAQUE=yes ;;
    --no-opaque) OPAQUE=no ;;
    *) echo "usage: $0 [--user] [--opaque|--no-opaque] [--uninstall]" >&2; exit 2 ;;
  esac
done
(( EUID == 0 )) && SUDO=

BIN=$PREFIX/bin/tuigler
DESKTOP=$PREFIX/share/applications/tuigler.desktop
ICON=$PREFIX/share/icons/hicolor/scalable/apps/tuigler.svg
MENU=${OMARCHY_MENU_FILE:-$HOME/.config/omarchy/extensions/omarchy-menu.jsonc}
MENU_DEFAULT=/usr/share/omarchy/config/omarchy/extensions/omarchy-menu.jsonc
HYPR=${HYPR_CONFIG_FILE:-$HOME/.config/hypr/hyprland.lua}

is_omarchy() { command -v omarchy-menu >/dev/null || [[ -d $HOME/.config/omarchy ]]; }

# Пункт меню лежит между маркерами, поэтому его можно обновлять и убирать,
# не задевая остальное содержимое файла.
backup() { cp "$1" "$1.bak.$(date +%s)"; }

menu_strip() { sed -i '/^\/\/ >>> tuigler >>>$/,/^\/\/ <<< tuigler <<<$/d' "$MENU"; }

menu_remove() {
  [[ -f $MENU ]] && grep -q '^// >>> tuigler >>>$' "$MENU" || return 0
  backup "$MENU"
  menu_strip
}

menu_add() {
  if [[ -f $MENU ]]; then
    backup "$MENU"
    menu_strip
  else
    mkdir -p "$(dirname "$MENU")"
    if [[ -f $MENU_DEFAULT ]]; then cp "$MENU_DEFAULT" "$MENU"; else printf '{\n}\n' >"$MENU"; fi
  fi
  if [[ $(head -n1 "$MENU") != "{" ]]; then
    echo "$MENU: первая строка не «{», пункт меню не добавлен" >&2
    return 0
  fi
  local icon entry
  icon=$(printf '\U000F02A0')   # nf-md-ghost
  entry="  \"tuigler\": {\"icon\": \"$icon\", \"label\": \"Tuigler\", \"aliases\": [\"tuigler\", \"fuggler\"], \"description\": \"tuigler — консольный уродец\", \"action\": \"omarchy-launch-or-focus-tui $BIN\"},"
  sed -i "1a // >>> tuigler >>>\n$entry\n// <<< tuigler <<<" "$MENU"
  echo "пункт Tuigler добавлен в меню Omarchy"
}

# Omarchy делает все окна слегка прозрачными на уровне композитора — из самой
# программы это не отключить, только правилом окна. Правило тоже лежит между
# маркерами. Класс окна — тот, что даёт omarchy-launch-or-focus-tui.
opaque_has() { [[ -f $HYPR ]] && grep -q '^-- >>> tuigler >>>$' "$HYPR"; }

opaque_strip() { sed -i '/^-- >>> tuigler >>>$/,/^-- <<< tuigler <<<$/d' "$HYPR"; }

opaque_remove() {
  opaque_has || return 0
  backup "$HYPR"
  opaque_strip
  echo "правило непрозрачности убрано из $HYPR"
}

opaque_add() {
  opaque_has && return 0
  backup "$HYPR"
  [[ -z $(tail -c1 "$HYPR") ]] || echo >>"$HYPR"
  cat >>"$HYPR" <<'EOF'
-- >>> tuigler >>>
o.window("org.omarchy.tuigler", { tag = "-default-opacity", opacity = "1 1" })
-- <<< tuigler <<<
EOF
  echo "окно tuigler теперь непрозрачное (правило в $HYPR)"
}

opaque_setup() {
  # правило написано под Lua-конфиг Omarchy; в других местах ему делать нечего
  [[ -f $HYPR ]] && grep -q 'default\.hypr' "$HYPR" || return 0
  local want=$OPAQUE reply def=n
  if [[ $want == ask ]]; then
    [[ -t 0 ]] || return 0      # без терминала не спрашиваем и ничего не меняем
    if opaque_has; then def=y; fi
    read -rp "Сделать окно tuigler непрозрачным (правило в hyprland.lua)? [$([[ $def == y ]] && echo Y/n || echo y/N)] " reply
    case ${reply:-$def} in
      [yYдД]*) want=yes ;;
      *) want=no ;;
    esac
  fi
  if [[ $want == yes ]]; then opaque_add; else opaque_remove; fi
}

refresh_caches() {
  if command -v gtk-update-icon-cache >/dev/null; then
    $SUDO gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true
  fi
  if command -v update-desktop-database >/dev/null; then
    $SUDO update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
  fi
}

if [[ $ACTION == uninstall ]]; then
  $SUDO rm -f "$BIN" "$DESKTOP" "$ICON"
  refresh_caches
  if is_omarchy; then menu_remove; opaque_remove; fi
  echo "tuigler удалён из $PREFIX (настройки в ~/.config/tuigler не тронуты)"
  exit 0
fi

if ! command -v python3 >/dev/null; then
  if command -v pacman >/dev/null; then hint="sudo pacman -S python"; else hint="sudo apt install python3"; fi
  echo "нужен python3: $hint" >&2
  exit 1
fi

# в ярлыке — полный путь: у лаунчера в PATH может не быть $PREFIX/bin
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
sed "s|^Exec=.*|Exec=$BIN|" packaging/tuigler.desktop >"$tmp"

$SUDO install -Dm755 tuigler.py "$BIN"
$SUDO install -Dm644 "$tmp" "$DESKTOP"
$SUDO install -Dm644 packaging/tuigler.svg "$ICON"
refresh_caches
if is_omarchy; then menu_add; opaque_setup; fi

echo "готово: $BIN"
case ":$PATH:" in
  *":$PREFIX/bin:"*) ;;
  *) echo "внимание: $PREFIX/bin нет в PATH — запускай по полному пути или добавь его" ;;
esac
