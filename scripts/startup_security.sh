#!/usr/bin/env bash
# Shared preflight policy for local service startup. This file intentionally
# contains only environment/host predicates so it can be tested without
# starting a server or occupying a port.

startup_security_is_truthy() {
    case "${1:-}" in
        true|TRUE|True|1|yes|YES|Yes|on|ON|On) return 0 ;;
        *) return 1 ;;
    esac
}

startup_security_is_loopback_host() {
    local host="${1:-}"
    host="${host#[}"
    host="${host%]}"
    case "$host" in
        localhost|127.*|::1) return 0 ;;
        *) return 1 ;;
    esac
}

startup_security_auth_mode() {
    if startup_security_is_truthy "${AGENT_PLATFORM_AUTH_ENABLED:-}" \
        && [ -n "${AGENT_PLATFORM_API_TOKEN:-}" ]; then
        printf '%s\n' 'api_token'
        return 0
    fi
    if startup_security_is_truthy "${AGENT_PLATFORM_IDENTITY_ENABLED:-}"; then
        printf '%s\n' 'identity'
        return 0
    fi
    local login_flag="${AGENT_PLATFORM_LOGIN_ENABLED:-}"
    if { [ -z "$login_flag" ] || startup_security_is_truthy "$login_flag"; } \
        && [ -n "${AGENT_PLATFORM_LOGIN_USERNAME:-}" ] \
        && [ -n "${AGENT_PLATFORM_LOGIN_PASSWORD:-}" ]; then
        printf '%s\n' 'login'
        return 0
    fi
    return 1
}

startup_security_validate_network_exposure() {
    local backend_host="${1:?backend host is required}"
    local frontend_host="${2:?frontend host is required}"
    local mode=""

    if startup_security_is_loopback_host "$backend_host" \
        && startup_security_is_loopback_host "$frontend_host"; then
        return 0
    fi

    mode="$(startup_security_auth_mode || true)"
    if [ -n "$mode" ]; then
        printf '%s\n' "[security] Network listener allowed with ${mode} authentication."
        return 0
    fi

    if startup_security_is_truthy "${AGENT_PLATFORM_ALLOW_UNAUTHENTICATED_NETWORK:-}"; then
        printf '%s\n' "[security] DANGER: unauthenticated network listener explicitly allowed by AGENT_PLATFORM_ALLOW_UNAUTHENTICATED_NETWORK=true." >&2
        return 0
    fi

    printf '%s\n' "[security] Refusing network listener without effective API token, login, or identity authentication. Use loopback hosts, configure authentication, or explicitly set AGENT_PLATFORM_ALLOW_UNAUTHENTICATED_NETWORK=true for trusted development only." >&2
    return 1
}
