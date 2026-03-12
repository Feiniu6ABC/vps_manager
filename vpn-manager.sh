#!/bin/bash
# VPN 用户订阅管理系统 - 基于 sing-box 的多用户订阅管理
# 配合 sb.sh (sing-box-yg) 使用，实现用户套餐、订阅链接、流量管理

export LANG=en_US.UTF-8
red(){ echo -e "\033[31m\033[01m$1\033[0m"; }
green(){ echo -e "\033[32m\033[01m$1\033[0m"; }
yellow(){ echo -e "\033[33m\033[01m$1\033[0m"; }
blue(){ echo -e "\033[36m\033[01m$1\033[0m"; }
white(){ echo -e "\033[37m\033[01m$1\033[0m"; }
readp(){ read -p "$(yellow "$1")" $2; }

[[ $EUID -ne 0 ]] && yellow "请以root模式运行脚本" && exit 1

# ======================== 全局变量 ========================
MANAGER_DIR="/etc/vpn-manager"
USERS_DB="$MANAGER_DIR/users.json"
PLANS_DB="$MANAGER_DIR/plans.json"
CONFIG_FILE="$MANAGER_DIR/config.json"
SUBS_DIR="$MANAGER_DIR/subs"
TRAFFIC_SNAP="$MANAGER_DIR/traffic_snap.json"
SB_DIR="/etc/s-box"
SB_CONFIG="$SB_DIR/sb.json"
SB_BIN="$SB_DIR/sing-box"
SCRIPT_PATH=$(readlink -f "$0")
LOCK_FILE="$MANAGER_DIR/.lock"

# ======================== 工具函数 ========================
sbjq(){ sed 's://.*::g' "$1" | jq "$2"; }

check_singbox(){
    if [[ ! -f "$SB_BIN" ]] || [[ ! -f "$SB_CONFIG" ]]; then
        red "未检测到 sing-box 安装，请先运行 sb.sh 安装 sing-box"
        exit 1
    fi
}

gen_uuid(){ $SB_BIN generate uuid; }
gen_token(){ openssl rand -hex 16; }
now_ts(){ date +%s; }

acquire_lock(){
    exec 200>"$LOCK_FILE"
    flock -n 200 || { red "另一个实例正在运行"; exit 1; }
}

# ======================== 初始化 ========================
init_manager(){
    check_singbox
    mkdir -p "$MANAGER_DIR" "$SUBS_DIR"

    # 初始化套餐配置
    if [[ ! -f "$PLANS_DB" ]]; then
        cat > "$PLANS_DB" <<'EOFPLANS'
{
  "plans": [
    {
      "id": 1,
      "name": "单日套餐",
      "duration_hours": 24,
      "traffic_gb": 10,
      "bandwidth_mbps": 20
    },
    {
      "id": 2,
      "name": "单月订阅",
      "duration_hours": 720,
      "traffic_gb": 100,
      "bandwidth_mbps": 50
    },
    {
      "id": 3,
      "name": "单月会员升级版",
      "duration_hours": 720,
      "traffic_gb": 200,
      "bandwidth_mbps": 100
    }
  ]
}
EOFPLANS
        green "套餐配置已初始化"
    fi

    # 初始化用户数据库
    if [[ ! -f "$USERS_DB" ]]; then
        echo '{"users":[]}' | jq . > "$USERS_DB"
        green "用户数据库已初始化"
    fi

    # 初始化全局配置
    if [[ ! -f "$CONFIG_FILE" ]]; then
        cat > "$CONFIG_FILE" <<'EOFCONF'
{
  "sub_port": 8888,
  "api_secret": "",
  "node_name": "VPN",
  "protocols": ["vless-reality"]
}
EOFCONF
        green "全局配置已初始化"
    fi

    # 初始化流量快照
    [[ ! -f "$TRAFFIC_SNAP" ]] && echo '{}' > "$TRAFFIC_SNAP"

    # 导入原始用户（首次运行时）
    local existing_uuid=$(sbjq "$SB_CONFIG" '.inbounds[0].users[0].uuid // empty' 2>/dev/null)
    local user_count=$(jq '.users | length' "$USERS_DB")
    if [[ -n "$existing_uuid" ]] && [[ "$user_count" -eq 0 ]]; then
        yellow "检测到现有 sing-box UUID: $existing_uuid"
        readp "是否导入为管理员用户？(Y/n): " import_yn
        if [[ -z "$import_yn" ]] || [[ "$import_yn" =~ ^[Yy]$ ]]; then
            local token=$(gen_token)
            local now=$(now_ts)
            local expires=$((now + 365 * 24 * 3600))
            jq --arg uuid "$existing_uuid" \
               --arg token "$token" \
               --argjson created "$now" \
               --argjson expires "$expires" \
               '.users += [{
                 "id": "admin",
                 "uuid": $uuid,
                 "token": $token,
                 "plan_id": 0,
                 "remark": "管理员",
                 "created_at": $created,
                 "expires_at": $expires,
                 "traffic_limit_bytes": 0,
                 "traffic_up_bytes": 0,
                 "traffic_down_bytes": 0,
                 "traffic_used_bytes": 0,
                 "status": "active"
               }]' "$USERS_DB" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$USERS_DB"
            green "管理员用户已导入 (无流量限制)"
        fi
    fi

    # 安装快捷命令
    if [[ ! -f /usr/bin/vpn-manager ]] || [[ "$(readlink -f /usr/bin/vpn-manager 2>/dev/null)" != "$SCRIPT_PATH" ]]; then
        ln -sf "$SCRIPT_PATH" /usr/bin/vpn-manager 2>/dev/null
        green "已安装快捷命令: vpn-manager"
    fi

    # 生成订阅服务器
    create_sub_server
    # 设置流量检查定时任务
    setup_traffic_cron
    green "VPN 管理系统初始化完成"
}

# ======================== 套餐管理 ========================
show_plans(){
    echo
    white "┌─────────────────────────────────────────────┐"
    white "│              可用订阅套餐                     │"
    white "├────┬────────────────┬────────┬───────────────┤"
    printf "│ %-2s │ %-14s │ %-6s │ %-6s │ %-8s │\n" "ID" "名称" "时长" "流量" "带宽"
    white "├────┼────────────────┼────────┼────────┼──────────┤"
    jq -r '.plans[] | "│ \(.id)  │ \(.name) │ \(.duration_hours)h │ \(.traffic_gb)GB │ \(.bandwidth_mbps)Mbps │"' "$PLANS_DB" | while read -r line; do
        printf "%-55s\n" "$line"
    done
    white "└────┴────────────────┴────────┴────────┴──────────┘"
}

edit_plan(){
    show_plans
    readp "输入要修改的套餐ID (1-3): " pid
    local plan=$(jq ".plans[] | select(.id == $pid)" "$PLANS_DB" 2>/dev/null)
    [[ -z "$plan" ]] && red "无效套餐ID" && return

    local cur_name=$(echo "$plan" | jq -r '.name')
    local cur_hours=$(echo "$plan" | jq -r '.duration_hours')
    local cur_gb=$(echo "$plan" | jq -r '.traffic_gb')

    readp "套餐名称 [$cur_name]: " new_name
    readp "有效时长(小时) [$cur_hours]: " new_hours
    readp "流量限额(GB) [$cur_gb]: " new_gb
    local cur_bw=$(echo "$plan" | jq -r '.bandwidth_mbps')
    readp "带宽限制(Mbps) [$cur_bw]: " new_bw

    new_name=${new_name:-$cur_name}
    new_hours=${new_hours:-$cur_hours}
    new_gb=${new_gb:-$cur_gb}
    new_bw=${new_bw:-$cur_bw}

    jq --argjson pid "$pid" --arg name "$new_name" \
       --argjson hours "$new_hours" --argjson gb "$new_gb" --argjson bw "$new_bw" \
       '(.plans[] | select(.id == $pid)) |= (.name = $name | .duration_hours = $hours | .traffic_gb = $gb | .bandwidth_mbps = $bw)' \
       "$PLANS_DB" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$PLANS_DB"
    green "套餐 $pid 已更新"
}

# ======================== 服务器参数读取 ========================
load_sb_params(){
    [[ ! -f "$SB_CONFIG" ]] && return 1

    SERVER_IP=$(cat "$SB_DIR/server_ip.log" 2>/dev/null)
    SERVER_IPCL=$(cat "$SB_DIR/server_ipcl.log" 2>/dev/null)
    REALITY_PUBKEY=$(cat "$SB_DIR/public.key" 2>/dev/null)
    ARGO_DOMAIN=$(cat "$SB_DIR/sbargoym.log" 2>/dev/null)
    HOSTNAME_TAG=$(hostname)

    # VLESS Reality (inbound 0)
    VL_PORT=$(sbjq "$SB_CONFIG" '.inbounds[0].listen_port // empty')
    VL_SNI=$(sbjq "$SB_CONFIG" '.inbounds[0].tls.server_name // empty')
    VL_SID=$(sbjq "$SB_CONFIG" '.inbounds[0].tls.reality.short_id[0] // empty')

    # VMess WS (inbound 1)
    VM_PORT=$(sbjq "$SB_CONFIG" '.inbounds[1].listen_port // empty')
    VM_PATH=$(sbjq "$SB_CONFIG" '.inbounds[1].transport.path // empty')
    VM_TLS=$(sbjq "$SB_CONFIG" '.inbounds[1].tls.enabled // false')
    VM_SNI=$(sbjq "$SB_CONFIG" '.inbounds[1].tls.server_name // empty')

    # VMess 地址逻辑（参照 sb.sh）
    if [[ "$VM_TLS" == "false" ]]; then
        if [[ -f "$SB_DIR/cfymjx.txt" ]]; then
            VM_SNI=$(cat "$SB_DIR/cfymjx.txt" 2>/dev/null)
        fi
        VM_ADD="$SERVER_IPCL"
        VM_ADD_SHARE="$SERVER_IP"
    else
        VM_ADD="$VM_SNI"
        VM_ADD_SHARE="$VM_SNI"
    fi
    [[ -f "$SB_DIR/cfvmadd_local.txt" ]] && VM_ADD=$(cat "$SB_DIR/cfvmadd_local.txt") && VM_ADD_SHARE="$VM_ADD"

    # Argo CDN 优选地址
    VM_ARGO_ADD="www.visa.com.sg"
    [[ -f "$SB_DIR/cfvmadd_argo.txt" ]] && VM_ARGO_ADD=$(cat "$SB_DIR/cfvmadd_argo.txt")

    # Hysteria2 (inbound 2)
    HY2_PORT=$(sbjq "$SB_CONFIG" '.inbounds[2].listen_port // empty')
    local hy2_keypath=$(sbjq "$SB_CONFIG" '.inbounds[2].tls.key_path // empty')
    if [[ "$hy2_keypath" == "/etc/s-box/private.key" ]]; then
        HY2_SNI="www.bing.com"
        HY2_ADDR="$SERVER_IP"
        HY2_INS=1
    else
        local ym=$(cat /root/ygkkkca/ca.log 2>/dev/null)
        HY2_SNI="$ym"
        HY2_ADDR="$ym"
        HY2_INS=0
    fi

    # Hysteria2 多端口跳跃
    HY2_MPORT=""
    local hy2_ports=$(iptables -t nat -nL --line 2>/dev/null | grep -w "$HY2_PORT" | awk '{print $8}' | sed 's/dpts://; s/dpt://' | tr '\n' ',' | sed 's/,$//')
    if [[ -n "$hy2_ports" ]]; then
        HY2_MPORT="${HY2_PORT},$(echo "$hy2_ports" | sed 's/:/-/g')"
    fi

    # TUIC (inbound 3)
    TUIC_PORT=$(sbjq "$SB_CONFIG" '.inbounds[3].listen_port // empty')
    local tuic_keypath=$(sbjq "$SB_CONFIG" '.inbounds[3].tls.key_path // empty')
    if [[ "$tuic_keypath" == "/etc/s-box/private.key" ]]; then
        TUIC_SNI="www.bing.com"
        TUIC_ADDR="$SERVER_IP"
        TUIC_INS=1
    else
        local ym=$(cat /root/ygkkkca/ca.log 2>/dev/null)
        TUIC_SNI="$ym"
        TUIC_ADDR="$ym"
        TUIC_INS=0
    fi

    # AnyTLS (inbound 4, 仅 sb11)
    ANYTLS_PORT=$(sbjq "$SB_CONFIG" '.inbounds[4].listen_port // empty' 2>/dev/null)
    if [[ -n "$ANYTLS_PORT" ]] && [[ "$ANYTLS_PORT" != "null" ]]; then
        local an_keypath=$(sbjq "$SB_CONFIG" '.inbounds[4].tls.key_path // empty')
        if [[ "$an_keypath" == "/etc/s-box/private.key" ]]; then
            ANYTLS_SNI="www.bing.com"
            ANYTLS_ADDR="$SERVER_IP"
            ANYTLS_INS=1
        else
            local ym=$(cat /root/ygkkkca/ca.log 2>/dev/null)
            ANYTLS_SNI="$ym"
            ANYTLS_ADDR="$ym"
            ANYTLS_INS=0
        fi
    else
        ANYTLS_PORT=""
    fi
}

# ======================== 订阅链接生成 ========================
gen_vless_link(){
    local uuid=$1 tag=$2
    [[ -z "$VL_PORT" ]] && return
    echo "vless://${uuid}@${SERVER_IP}:${VL_PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=${VL_SNI}&fp=chrome&pbk=${REALITY_PUBKEY}&sid=${VL_SID}&type=tcp&headerType=none#${tag}-vl-reality"
}

gen_vmess_link(){
    local uuid=$1 tag=$2
    [[ -z "$VM_PORT" ]] && return
    local tls_str=""
    [[ "$VM_TLS" == "true" ]] && tls_str="tls"
    local json='{"add":"'$VM_ADD_SHARE'","aid":"0","host":"'$VM_SNI'","id":"'$uuid'","net":"ws","path":"'$VM_PATH'","port":"'$VM_PORT'","ps":"'${tag}-vm-ws'","tls":"'$tls_str'","sni":"'$VM_SNI'","fp":"chrome","type":"none","v":"2"}'
    echo "vmess://$(echo -n "$json" | base64 -w 0)"
}

gen_vmess_argo_link(){
    local uuid=$1 tag=$2 domain=$3
    [[ -z "$VM_PORT" ]] || [[ -z "$domain" ]] && return
    local json='{"add":"'$VM_ARGO_ADD'","aid":"0","host":"'$domain'","id":"'$uuid'","net":"ws","path":"'$VM_PATH'","port":"8443","ps":"'${tag}-vm-argo'","tls":"tls","sni":"'$domain'","fp":"chrome","type":"none","v":"2"}'
    echo "vmess://$(echo -n "$json" | base64 -w 0)"
}

gen_hy2_link(){
    local uuid=$1 tag=$2
    [[ -z "$HY2_PORT" ]] && return
    local mport_str=""
    [[ -n "$HY2_MPORT" ]] && mport_str="&mport=${HY2_MPORT}"
    echo "hysteria2://${uuid}@${HY2_ADDR}:${HY2_PORT}?security=tls&alpn=h3&insecure=${HY2_INS}${mport_str}&sni=${HY2_SNI}#${tag}-hy2"
}

gen_tuic_link(){
    local uuid=$1 tag=$2
    [[ -z "$TUIC_PORT" ]] && return
    echo "tuic://${uuid}:${uuid}@${TUIC_ADDR}:${TUIC_PORT}?congestion_control=bbr&udp_relay_mode=native&alpn=h3&sni=${TUIC_SNI}&allow_insecure=${TUIC_INS}&allowInsecure=${TUIC_INS}#${tag}-tuic5"
}

gen_anytls_link(){
    local uuid=$1 tag=$2
    [[ -z "$ANYTLS_PORT" ]] && return
    echo "anytls://${uuid}@${ANYTLS_ADDR}:${ANYTLS_PORT}?sni=${ANYTLS_SNI}&allowInsecure=${ANYTLS_INS}#${tag}-anytls"
}

gen_user_sub(){
    local user_id=$1
    local user=$(jq -r ".users[] | select(.id==\"$user_id\")" "$USERS_DB")
    [[ -z "$user" ]] && return 1
    local uuid=$(echo "$user" | jq -r '.uuid')
    local token=$(echo "$user" | jq -r '.token')
    local remark=$(echo "$user" | jq -r '.remark // "Node"')
    local tag="${remark}"

    load_sb_params

    # 读取启用的协议 (默认仅 vless-reality)
    local protocols=$(jq -r '.protocols[]? // empty' "$CONFIG_FILE" 2>/dev/null)
    [[ -z "$protocols" ]] && protocols="vless-reality"

    local links=""
    local link

    for proto in $protocols; do
        case "$proto" in
            vless-reality)
                link=$(gen_vless_link "$uuid" "$tag")
                [[ -n "$link" ]] && links="${links}${link}\n"
                ;;
            vmess-ws)
                link=$(gen_vmess_link "$uuid" "$tag")
                [[ -n "$link" ]] && links="${links}${link}\n"
                # Argo 固定隧道
                if [[ -n "$ARGO_DOMAIN" ]]; then
                    link=$(gen_vmess_argo_link "$uuid" "$tag" "$ARGO_DOMAIN")
                    [[ -n "$link" ]] && links="${links}${link}\n"
                fi
                # Argo 临时隧道
                local argo_tmp=$(cat "$SB_DIR/argo.log" 2>/dev/null | grep -a trycloudflare.com | awk 'NR==2{print}' | awk -F// '{print $2}' | awk '{print $1}')
                if [[ -n "$argo_tmp" ]]; then
                    link=$(gen_vmess_argo_link "$uuid" "$tag" "$argo_tmp")
                    [[ -n "$link" ]] && links="${links}${link}\n"
                fi
                ;;
            hysteria2)
                link=$(gen_hy2_link "$uuid" "$tag")
                [[ -n "$link" ]] && links="${links}${link}\n"
                ;;
            tuic)
                link=$(gen_tuic_link "$uuid" "$tag")
                [[ -n "$link" ]] && links="${links}${link}\n"
                ;;
            anytls)
                link=$(gen_anytls_link "$uuid" "$tag")
                [[ -n "$link" ]] && links="${links}${link}\n"
                ;;
        esac
    done

    mkdir -p "$SUBS_DIR"
    echo -en "$links" | base64 -w 0 > "${SUBS_DIR}/${token}.txt"
}

gen_all_subs(){
    load_sb_params
    local ids=$(jq -r '.users[] | select(.status=="active") | .id' "$USERS_DB")
    for uid in $ids; do
        gen_user_sub "$uid"
    done
    green "所有订阅已刷新"
}

# ======================== 带宽限速 ========================

# 方案一: sing-box 1.11+ 路由规则限速 (精确按 UUID 分组)
inject_speed_limits(){
    local sbnh=$($SB_BIN version 2>/dev/null | awk '/version/{print $NF}' | cut -d '.' -f 1,2)

    # 仅 1.11+ 支持 route rule speed_limit
    if [[ "$sbnh" == "1.10" ]]; then
        setup_iptables_bw_limit
        return
    fi

    for config_file in "$SB_DIR/sb.json" "$SB_DIR/sb11.json"; do
        [[ ! -f "$config_file" ]] && continue

        local tmp=$(mktemp)
        sed 's://.*::g' "$config_file" > "$tmp"

        # 检查是否有 route.rules
        local has_route=$(jq 'has("route") and (.route | has("rules"))' "$tmp" 2>/dev/null)
        [[ "$has_route" != "true" ]] && rm -f "$tmp" && continue

        # 移除已有的限速规则 (有 speed_limit 且有 auth_user 的规则)
        local cleaned=$(jq '.route.rules |= [.[] | select((has("speed_limit") and has("auth_user")) | not)]' "$tmp")

        # 按套餐带宽分组构建限速规则
        local speed_rules="[]"
        local plan_ids=$(jq -r '.plans[].id' "$PLANS_DB")
        for pid in $plan_ids; do
            local bw=$(jq -r ".plans[] | select(.id == $pid) | .bandwidth_mbps" "$PLANS_DB")
            [[ -z "$bw" ]] || [[ "$bw" == "null" ]] && continue
            local uuids=$(jq -c "[.users[] | select(.status==\"active\" and .plan_id == $pid) | .uuid]" "$USERS_DB")
            local count=$(echo "$uuids" | jq 'length')
            [[ "$count" -eq 0 ]] && continue

            speed_rules=$(echo "$speed_rules" | jq \
                --argjson uuids "$uuids" \
                --arg bw "${bw} mbps" \
                '. + [{"auth_user": $uuids, "action": "route", "outbound": "direct", "speed_limit": $bw}]')
        done

        # 将限速规则追加到 route.rules 末尾 (在 final 之前生效)
        echo "$cleaned" | jq --argjson sr "$speed_rules" '.route.rules += $sr' > "$config_file"
        rm -f "$tmp"
    done
}

remove_speed_limit_rules(){
    for config_file in "$SB_DIR/sb.json" "$SB_DIR/sb11.json"; do
        [[ ! -f "$config_file" ]] && continue
        local tmp=$(mktemp)
        sed 's://.*::g' "$config_file" > "$tmp"
        jq '.route.rules |= [.[] | select((has("speed_limit") and has("auth_user")) | not)]' "$tmp" > "$config_file"
        rm -f "$tmp"
    done
}

# 方案二: iptables hashlimit 限速 (1.10 回退方案，按客户端 IP)
setup_iptables_bw_limit(){
    local vl_port=$(sbjq "$SB_CONFIG" '.inbounds[0].listen_port // empty')
    [[ -z "$vl_port" ]] && return

    # 取最高套餐带宽作为 per-IP 上限
    local max_bw=$(jq '[.plans[].bandwidth_mbps] | max' "$PLANS_DB")
    [[ -z "$max_bw" ]] || [[ "$max_bw" == "null" ]] && max_bw=100
    local max_kbps=$((max_bw * 125))  # Mbps → KB/s

    # 清理旧规则
    iptables -D OUTPUT -p tcp --sport "$vl_port" -j VLESS_BW 2>/dev/null
    iptables -F VLESS_BW 2>/dev/null
    iptables -X VLESS_BW 2>/dev/null

    # 创建限速链
    iptables -N VLESS_BW 2>/dev/null
    iptables -A VLESS_BW -m hashlimit \
        --hashlimit-above "${max_kbps}kb/sec" \
        --hashlimit-burst "$((max_kbps * 2))" \
        --hashlimit-mode dstip \
        --hashlimit-name vless_bw \
        --hashlimit-htable-expire 60000 \
        -j DROP
    iptables -I OUTPUT -p tcp --sport "$vl_port" -j VLESS_BW

    yellow "iptables 限速已设置: 每客户端 IP 最高 ${max_bw}Mbps"
    yellow "注: sing-box 1.10 不支持按用户精确限速，建议升级到 1.11+"
}

# ======================== Sing-box 配置同步 ========================
sync_users_to_sb(){
    acquire_lock

    local active_uuids=$(jq -c '[.users[] | select(.status=="active") | .uuid]' "$USERS_DB")
    local num_active=$(echo "$active_uuids" | jq 'length')

    # 至少保留一个占位 UUID，防止 sing-box 空用户数组报错
    if [[ "$num_active" -eq 0 ]]; then
        active_uuids='["00000000-0000-0000-0000-000000000000"]'
    fi

    # 为每种协议构建 users 数组
    local vless_users=$(echo "$active_uuids" | jq '[.[] | {"uuid": ., "flow": "xtls-rprx-vision"}]')
    local vmess_users=$(echo "$active_uuids" | jq '[.[] | {"uuid": ., "alterId": 0}]')
    local tuic_users=$(echo "$active_uuids" | jq '[.[] | {"uuid": ., "password": .}]')
    local anytls_users=$(echo "$active_uuids" | jq '[.[] | {"password": .}]')

    local hy2_users=$(echo "$active_uuids" | jq '[.[] | {"password": .}]')

    for config_file in "$SB_DIR/sb.json" "$SB_DIR/sb10.json" "$SB_DIR/sb11.json"; do
        [[ ! -f "$config_file" ]] && continue

        local tmp=$(mktemp)
        # 去除 // 注释后解析
        sed 's://.*::g' "$config_file" > "$tmp"

        local num_inbounds=$(jq '.inbounds | length' "$tmp" 2>/dev/null)
        [[ -z "$num_inbounds" ]] && rm -f "$tmp" && continue

        local result=$(cat "$tmp")
        for ((i=0; i<num_inbounds; i++)); do
            local itype=$(echo "$result" | jq -r ".inbounds[$i].type")
            case "$itype" in
                vless)
                    result=$(echo "$result" | jq --argjson u "$vless_users" ".inbounds[$i].users = \$u")
                    ;;
                vmess)
                    result=$(echo "$result" | jq --argjson u "$vmess_users" ".inbounds[$i].users = \$u")
                    ;;
                hysteria2)
                    result=$(echo "$result" | jq --argjson u "$hy2_users" ".inbounds[$i].users = \$u")
                    ;;
                tuic)
                    result=$(echo "$result" | jq --argjson u "$tuic_users" ".inbounds[$i].users = \$u")
                    ;;
                anytls)
                    result=$(echo "$result" | jq --argjson u "$anytls_users" ".inbounds[$i].users = \$u")
                    ;;
            esac
        done
        echo "$result" | jq . > "$config_file"
        rm -f "$tmp"
    done

    # 注入带宽限速规则
    inject_speed_limits

    # 验证配置
    if $SB_BIN check -c "$SB_CONFIG" >/dev/null 2>&1; then
        systemctl restart sing-box 2>/dev/null || rc-service sing-box restart 2>/dev/null
        green "sing-box 配置已同步并重启"
    else
        red "sing-box 配置验证失败，尝试回退限速规则..."
        # 回退：移除限速规则再验证
        remove_speed_limit_rules
        if $SB_BIN check -c "$SB_CONFIG" >/dev/null 2>&1; then
            # 改用 iptables 限速
            setup_iptables_bw_limit
            systemctl restart sing-box 2>/dev/null || rc-service sing-box restart 2>/dev/null
            yellow "sing-box 已重启 (限速通过 iptables 实现)"
        else
            red "sing-box 配置验证失败！请检查配置文件"
            $SB_BIN check -c "$SB_CONFIG"
            return 1
        fi
    fi
}

# ======================== 用户管理 ========================
add_user(){
    show_plans
    echo
    readp "选择套餐 [1-3]: " plan_id
    local plan=$(jq ".plans[] | select(.id == ${plan_id:-0})" "$PLANS_DB" 2>/dev/null)
    [[ -z "$plan" ]] && red "无效套餐" && return

    readp "用户备注 (可选，回车跳过): " remark
    remark=${remark:-"用户$(date +%m%d%H%M)"}

    local uuid=$(gen_uuid)
    local token=$(gen_token)
    local now=$(now_ts)
    local duration_hours=$(echo "$plan" | jq -r '.duration_hours')
    local traffic_gb=$(echo "$plan" | jq -r '.traffic_gb')
    local expires=$((now + duration_hours * 3600))
    local traffic_limit=$((traffic_gb * 1073741824))
    local user_id="u_$(openssl rand -hex 4)"

    jq --arg id "$user_id" \
       --arg uuid "$uuid" \
       --arg token "$token" \
       --argjson plan_id "$plan_id" \
       --arg remark "$remark" \
       --argjson created "$now" \
       --argjson expires "$expires" \
       --argjson limit "$traffic_limit" \
       '.users += [{
         "id": $id,
         "uuid": $uuid,
         "token": $token,
         "plan_id": $plan_id,
         "remark": $remark,
         "created_at": $created,
         "expires_at": $expires,
         "traffic_limit_bytes": $limit,
         "traffic_up_bytes": 0,
         "traffic_down_bytes": 0,
         "traffic_used_bytes": 0,
         "status": "active"
       }]' "$USERS_DB" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$USERS_DB"

    # 同步到 sing-box 并生成订阅
    sync_users_to_sb
    gen_user_sub "$user_id"

    # 显示结果
    local plan_name=$(echo "$plan" | jq -r '.name')
    local sub_port=$(jq -r '.sub_port' "$CONFIG_FILE")
    local server_ip=$(cat "$SB_DIR/server_ipcl.log" 2>/dev/null)
    local sub_url="http://${server_ip}:${sub_port}/sub/${token}"

    echo
    green "=========================================="
    green "  用户创建成功！"
    green "=========================================="
    echo "用户ID:    $user_id"
    echo "UUID:      $uuid"
    echo "备注:      $remark"
    echo "套餐:      $plan_name"
    echo "有效期至:  $(date -d @$expires '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -r $expires '+%Y-%m-%d %H:%M:%S' 2>/dev/null)"
    echo "流量限额:  ${traffic_gb}GB"
    echo
    yellow "订阅链接:"
    echo "$sub_url"
    echo
    yellow "二维码:"
    qrencode -t ansiutf8 "$sub_url" 2>/dev/null || yellow "(qrencode 未安装，跳过二维码)"
    echo
}

del_user(){
    list_users
    echo
    readp "输入要删除的用户ID: " uid
    local user=$(jq -r ".users[] | select(.id==\"$uid\")" "$USERS_DB")
    [[ -z "$user" ]] && red "用户不存在" && return

    local remark=$(echo "$user" | jq -r '.remark')
    readp "确认删除用户 [$remark] ($uid)？(y/N): " confirm
    [[ ! "$confirm" =~ ^[Yy]$ ]] && yellow "已取消" && return

    jq "del(.users[] | select(.id==\"$uid\"))" "$USERS_DB" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$USERS_DB"

    # 删除订阅文件
    local token=$(echo "$user" | jq -r '.token')
    rm -f "${SUBS_DIR}/${token}.txt"

    sync_users_to_sb
    green "用户 $uid 已删除"
}

list_users(){
    local count=$(jq '.users | length' "$USERS_DB")
    if [[ "$count" -eq 0 ]]; then
        yellow "暂无用户"
        return
    fi

    echo
    white "用户列表 (共 ${count} 个):"
    echo "────────────────────────────────────────────────────────────────────────"
    printf "%-12s %-10s %-6s %-8s %-14s %-10s %s\n" "ID" "备注" "套餐" "状态" "过期时间" "已用流量" "限额"
    echo "────────────────────────────────────────────────────────────────────────"

    jq -r '.users[] | [.id, .remark, (.plan_id|tostring), .status, .expires_at, .traffic_used_bytes, .traffic_limit_bytes] | @tsv' "$USERS_DB" | \
    while IFS=$'\t' read -r id remark plan_id status expires used limit; do
        local exp_date=$(date -d @"$expires" '+%m-%d %H:%M' 2>/dev/null || date -r "$expires" '+%m-%d %H:%M' 2>/dev/null)
        local used_gb=$(echo "scale=2; $used / 1073741824" | bc 2>/dev/null || echo "0")
        local limit_gb=""
        if [[ "$limit" -eq 0 ]]; then
            limit_gb="无限"
        else
            limit_gb="$(echo "scale=0; $limit / 1073741824" | bc 2>/dev/null || echo "?")GB"
        fi

        local status_str=""
        case "$status" in
            active)    status_str="\033[32m在线\033[0m";;
            expired)   status_str="\033[31m过期\033[0m";;
            overlimit) status_str="\033[31m超额\033[0m";;
            disabled)  status_str="\033[33m禁用\033[0m";;
            *)         status_str="$status";;
        esac

        printf "%-12s %-10s %-6s " "$id" "$remark" "$plan_id"
        echo -en "$status_str"
        printf "   %-14s %-10s %s\n" "$exp_date" "${used_gb}GB" "$limit_gb"
    done
    echo "────────────────────────────────────────────────────────────────────────"
}

user_info(){
    readp "输入用户ID: " uid
    local user=$(jq ".users[] | select(.id==\"$uid\")" "$USERS_DB")
    [[ -z "$user" ]] && red "用户不存在" && return

    local uuid=$(echo "$user" | jq -r '.uuid')
    local token=$(echo "$user" | jq -r '.token')
    local remark=$(echo "$user" | jq -r '.remark')
    local plan_id=$(echo "$user" | jq -r '.plan_id')
    local status=$(echo "$user" | jq -r '.status')
    local created=$(echo "$user" | jq -r '.created_at')
    local expires=$(echo "$user" | jq -r '.expires_at')
    local limit=$(echo "$user" | jq -r '.traffic_limit_bytes')
    local used=$(echo "$user" | jq -r '.traffic_used_bytes')
    local up=$(echo "$user" | jq -r '.traffic_up_bytes')
    local down=$(echo "$user" | jq -r '.traffic_down_bytes')

    local plan_name="管理员(无限)"
    [[ "$plan_id" -gt 0 ]] && plan_name=$(jq -r ".plans[] | select(.id==$plan_id) | .name" "$PLANS_DB")

    local used_gb=$(echo "scale=2; $used / 1073741824" | bc 2>/dev/null || echo "0")
    local limit_gb="无限"
    [[ "$limit" -gt 0 ]] && limit_gb="$(echo "scale=0; $limit / 1073741824" | bc 2>/dev/null)GB"

    echo
    green "=========================================="
    green "  用户详情: $remark"
    green "=========================================="
    echo "用户ID:     $uid"
    echo "UUID:       $uuid"
    echo "套餐:       $plan_name"
    echo "状态:       $status"
    echo "创建时间:   $(date -d @$created '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -r $created '+%Y-%m-%d %H:%M:%S' 2>/dev/null)"
    echo "过期时间:   $(date -d @$expires '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -r $expires '+%Y-%m-%d %H:%M:%S' 2>/dev/null)"
    echo "流量限额:   $limit_gb"
    echo "已用流量:   ${used_gb}GB (上行: $(echo "scale=2; $up/1073741824" | bc 2>/dev/null || echo 0)GB / 下行: $(echo "scale=2; $down/1073741824" | bc 2>/dev/null || echo 0)GB)"
    echo

    local sub_port=$(jq -r '.sub_port' "$CONFIG_FILE")
    local server_ip=$(cat "$SB_DIR/server_ipcl.log" 2>/dev/null)
    local sub_url="http://${server_ip}:${sub_port}/sub/${token}"
    yellow "订阅链接:"
    echo "$sub_url"
    echo
    qrencode -t ansiutf8 "$sub_url" 2>/dev/null
}

renew_user(){
    list_users
    echo
    readp "输入要续费的用户ID: " uid
    local user=$(jq ".users[] | select(.id==\"$uid\")" "$USERS_DB")
    [[ -z "$user" ]] && red "用户不存在" && return

    show_plans
    echo
    readp "选择新套餐 [1-3]: " plan_id
    local plan=$(jq ".plans[] | select(.id == ${plan_id:-0})" "$PLANS_DB" 2>/dev/null)
    [[ -z "$plan" ]] && red "无效套餐" && return

    local now=$(now_ts)
    local duration_hours=$(echo "$plan" | jq -r '.duration_hours')
    local traffic_gb=$(echo "$plan" | jq -r '.traffic_gb')
    local expires=$((now + duration_hours * 3600))
    local traffic_limit=$((traffic_gb * 1073741824))

    jq --arg uid "$uid" \
       --argjson plan_id "$plan_id" \
       --argjson expires "$expires" \
       --argjson limit "$traffic_limit" \
       '(.users[] | select(.id == $uid)) |= (
         .plan_id = $plan_id |
         .expires_at = $expires |
         .traffic_limit_bytes = $limit |
         .traffic_up_bytes = 0 |
         .traffic_down_bytes = 0 |
         .traffic_used_bytes = 0 |
         .status = "active"
       )' "$USERS_DB" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$USERS_DB"

    sync_users_to_sb
    gen_user_sub "$uid"

    local plan_name=$(echo "$plan" | jq -r '.name')
    green "用户 $uid 已续费: $plan_name, 流量已重置, 有效期至 $(date -d @$expires '+%Y-%m-%d %H:%M:%S' 2>/dev/null)"
}

set_traffic(){
    readp "输入用户ID: " uid
    local user=$(jq ".users[] | select(.id==\"$uid\")" "$USERS_DB")
    [[ -z "$user" ]] && red "用户不存在" && return

    local cur_used=$(echo "$user" | jq -r '.traffic_used_bytes')
    local cur_gb=$(echo "scale=2; $cur_used / 1073741824" | bc 2>/dev/null || echo "0")
    yellow "当前已用流量: ${cur_gb}GB"
    readp "设置已用流量 (GB, 输入 0 重置): " new_gb
    [[ -z "$new_gb" ]] && return

    local new_bytes=$(echo "$new_gb * 1073741824 / 1" | bc 2>/dev/null)
    [[ -z "$new_bytes" ]] && red "无效输入" && return

    jq --arg uid "$uid" --argjson bytes "$new_bytes" \
       '(.users[] | select(.id == $uid)) |= (.traffic_used_bytes = $bytes | .traffic_up_bytes = 0 | .traffic_down_bytes = $bytes)' \
       "$USERS_DB" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$USERS_DB"
    green "用户 $uid 流量已更新为 ${new_gb}GB"
}

disable_user(){
    readp "输入用户ID: " uid
    local user=$(jq ".users[] | select(.id==\"$uid\")" "$USERS_DB")
    [[ -z "$user" ]] && red "用户不存在" && return

    local status=$(echo "$user" | jq -r '.status')
    if [[ "$status" == "active" ]]; then
        jq "(.users[] | select(.id==\"$uid\")).status = \"disabled\"" "$USERS_DB" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$USERS_DB"
        sync_users_to_sb
        green "用户 $uid 已禁用"
    else
        jq "(.users[] | select(.id==\"$uid\")).status = \"active\"" "$USERS_DB" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$USERS_DB"
        sync_users_to_sb
        gen_user_sub "$uid"
        green "用户 $uid 已启用"
    fi
}

# ======================== 订阅服务器 ========================
create_sub_server(){
    local sub_port=$(jq -r '.sub_port' "$CONFIG_FILE")

    cat > "$MANAGER_DIR/sub_server.py" <<'EOFPY'
#!/usr/bin/env python3
import http.server, json, os, sys, time, subprocess, secrets

MANAGER_DIR = "/etc/vpn-manager"
USERS_DB = os.path.join(MANAGER_DIR, "users.json")
PLANS_DB = os.path.join(MANAGER_DIR, "plans.json")
CONFIG_FILE = os.path.join(MANAGER_DIR, "config.json")
SUBS_DIR = os.path.join(MANAGER_DIR, "subs")
SB_DIR = "/etc/s-box"
SCRIPT_PATH = "/usr/bin/vpn-manager"

def load_json(path):
    with open(path) as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

class SubHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parts = self.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "sub":
            self.serve_sub(parts[1])
        else:
            self.send_error(404)

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "api" and parts[1] == "create":
            self.api_create()
        else:
            self.send_error(404)

    def serve_sub(self, token):
        try:
            db = load_json(USERS_DB)
        except:
            self.send_error(500, "Database error")
            return

        user = next((u for u in db["users"] if u["token"] == token), None)
        if not user:
            self.send_error(404, "Not found")
            return

        if user["status"] != "active":
            self.send_error(403, "Subscription inactive")
            return

        now = time.time()
        if user["expires_at"] < now:
            self.send_error(403, "Subscription expired")
            return

        limit = user["traffic_limit_bytes"]
        if limit > 0 and user["traffic_used_bytes"] >= limit:
            self.send_error(403, "Traffic limit exceeded")
            return

        sub_file = os.path.join(SUBS_DIR, f"{token}.txt")
        if not os.path.exists(sub_file):
            self.send_error(404, "Subscription file not found")
            return

        with open(sub_file) as f:
            content = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Profile-Update-Interval", "6")
        self.send_header("Content-Disposition", "attachment; filename=subscription")

        upload = user.get("traffic_up_bytes", 0)
        download = user.get("traffic_down_bytes", 0)
        total = limit if limit > 0 else 1099511627776
        expire = int(user["expires_at"])
        self.send_header("subscription-userinfo",
            f"upload={upload}; download={download}; total={total}; expire={expire}")

        self.end_headers()
        self.wfile.write(content.encode())

    def api_create(self):
        """发卡平台 Webhook: 自动创建用户并返回订阅链接"""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
        except:
            self.send_json(400, {"success": False, "error": "Invalid JSON"})
            return

        # 验证 API 密钥
        config = load_json(CONFIG_FILE)
        api_secret = config.get("api_secret", "")
        if not api_secret or body.get("secret") != api_secret:
            self.send_json(403, {"success": False, "error": "Invalid secret"})
            return

        # 获取套餐
        plan_id = body.get("plan_id", 1)
        plans = load_json(PLANS_DB)
        plan = next((p for p in plans["plans"] if p["id"] == plan_id), None)
        if not plan:
            self.send_json(400, {"success": False, "error": f"Invalid plan_id: {plan_id}"})
            return

        # 生成用户凭证
        uuid_result = subprocess.run([os.path.join(SB_DIR, "sing-box"), "generate", "uuid"],
                                     capture_output=True, text=True)
        uuid = uuid_result.stdout.strip()
        if not uuid:
            self.send_json(500, {"success": False, "error": "Failed to generate UUID"})
            return

        token = secrets.token_hex(16)
        now = int(time.time())
        expires = now + plan["duration_hours"] * 3600
        traffic_limit = plan["traffic_gb"] * 1073741824
        user_id = f"api_{secrets.token_hex(4)}"
        remark = body.get("remark", f"API-{plan['name']}")

        # 写入数据库
        db = load_json(USERS_DB)
        db["users"].append({
            "id": user_id,
            "uuid": uuid,
            "token": token,
            "plan_id": plan_id,
            "remark": remark,
            "created_at": now,
            "expires_at": expires,
            "traffic_limit_bytes": traffic_limit,
            "traffic_up_bytes": 0,
            "traffic_down_bytes": 0,
            "traffic_used_bytes": 0,
            "status": "active"
        })
        save_json(USERS_DB, db)

        # 同步 sing-box 配置和生成订阅 (串行执行确保顺序)
        script = SCRIPT_PATH if os.path.exists(SCRIPT_PATH) else None
        if script:
            subprocess.run(["bash", script, "--sync"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            subprocess.run(["bash", script, "--gen-subs"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)

        # 构建订阅链接
        server_ip = ""
        try:
            with open(os.path.join(SB_DIR, "server_ipcl.log")) as f:
                server_ip = f.read().strip()
        except:
            pass
        sub_port = config.get("sub_port", 8888)
        sub_url = f"http://{server_ip}:{sub_port}/sub/{token}"

        expire_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expires))

        self.send_json(200, {
            "success": True,
            "sub_url": sub_url,
            "user_id": user_id,
            "uuid": uuid,
            "plan": plan["name"],
            "traffic_gb": plan["traffic_gb"],
            "bandwidth_mbps": plan.get("bandwidth_mbps", 0),
            "expires": expire_str,
            "expires_ts": expires
        })

    def send_json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    server = http.server.HTTPServer(("0.0.0.0", port), SubHandler)
    print(f"Subscription server running on port {port}")
    server.serve_forever()
EOFPY
    chmod +x "$MANAGER_DIR/sub_server.py"

    # 创建 systemd 服务
    cat > /etc/systemd/system/vpn-sub.service <<EOF
[Unit]
Description=VPN Subscription Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $MANAGER_DIR/sub_server.py $sub_port
Restart=on-failure
RestartSec=5
WorkingDirectory=$MANAGER_DIR

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload 2>/dev/null
}

start_sub_server(){
    local sub_port=$(jq -r '.sub_port' "$CONFIG_FILE")
    systemctl start vpn-sub 2>/dev/null && systemctl enable vpn-sub 2>/dev/null
    if systemctl is-active vpn-sub >/dev/null 2>&1; then
        green "订阅服务器已启动 (端口: $sub_port)"
    else
        red "订阅服务器启动失败，尝试直接启动..."
        nohup python3 "$MANAGER_DIR/sub_server.py" "$sub_port" > "$MANAGER_DIR/sub_server.log" 2>&1 &
        echo $! > "$MANAGER_DIR/sub_server.pid"
        green "订阅服务器已启动 (PID: $!, 端口: $sub_port)"
    fi
}

stop_sub_server(){
    systemctl stop vpn-sub 2>/dev/null
    # 也清理可能的直接启动进程
    if [[ -f "$MANAGER_DIR/sub_server.pid" ]]; then
        kill $(cat "$MANAGER_DIR/sub_server.pid") 2>/dev/null
        rm -f "$MANAGER_DIR/sub_server.pid"
    fi
    pkill -f "sub_server.py" 2>/dev/null
    yellow "订阅服务器已停止"
}

sub_server_status(){
    if systemctl is-active vpn-sub >/dev/null 2>&1 || pgrep -f "sub_server.py" >/dev/null 2>&1; then
        local sub_port=$(jq -r '.sub_port' "$CONFIG_FILE")
        green "订阅服务器运行中 (端口: $sub_port)"
        return 0
    else
        red "订阅服务器未运行"
        return 1
    fi
}

sub_menu(){
    echo
    white "── 订阅服务器管理 ──"
    echo "1. 启动订阅服务器"
    echo "2. 停止订阅服务器"
    echo "3. 查看状态"
    echo "4. 修改订阅端口"
    echo "0. 返回"
    readp "请选择: " choice
    case "$choice" in
        1) start_sub_server;;
        2) stop_sub_server;;
        3) sub_server_status;;
        4)
            readp "输入新端口号: " new_port
            [[ -z "$new_port" ]] && return
            jq --argjson p "$new_port" '.sub_port = $p' "$CONFIG_FILE" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$CONFIG_FILE"
            create_sub_server
            stop_sub_server
            start_sub_server
            ;;
        *) return;;
    esac
}

# ======================== 流量管理 ========================
setup_traffic_cron(){
    cat > "$MANAGER_DIR/traffic_check.sh" <<'EOFCRON'
#!/bin/bash
# VPN 流量检查 & 过期处理 (每5分钟执行)
MANAGER_DIR="/etc/vpn-manager"
USERS_DB="$MANAGER_DIR/users.json"
SNAP_FILE="$MANAGER_DIR/traffic_snap.json"
SB_API="http://127.0.0.1:9090"
LOCK_FILE="$MANAGER_DIR/.lock"

exec 200>"$LOCK_FILE"
flock -n 200 || exit 0

[ ! -f "$USERS_DB" ] && exit 0
[ ! -f "$SNAP_FILE" ] && echo '{"upload":0,"download":0}' > "$SNAP_FILE"

# 查询 clash API 获取总流量
conns=$(curl -s --max-time 5 "$SB_API/connections" 2>/dev/null)
[ -z "$conns" ] && exit 0

total_up=$(echo "$conns" | jq '.uploadTotal // 0' 2>/dev/null)
total_down=$(echo "$conns" | jq '.downloadTotal // 0' 2>/dev/null)
[ -z "$total_up" ] && exit 0

# 读取上次快照
prev_up=$(jq -r '.upload // 0' "$SNAP_FILE")
prev_down=$(jq -r '.download // 0' "$SNAP_FILE")

# 计算增量（处理 sing-box 重启导致计数器归零）
if [ "$total_up" -lt "$prev_up" ] 2>/dev/null; then
    delta_up=$total_up
    delta_down=$total_down
else
    delta_up=$((total_up - prev_up))
    delta_down=$((total_down - prev_down))
fi

# 保存当前快照
echo "{\"upload\":$total_up,\"download\":$total_down}" > "$SNAP_FILE"

# 将流量均分给活跃用户（管理员除外）
enabled_count=$(jq '[.users[] | select(.status=="active" and .plan_id > 0)] | length' "$USERS_DB")
if [ "$enabled_count" -gt 0 ] && [ "$((delta_up + delta_down))" -gt 0 ]; then
    per_user_up=$((delta_up / enabled_count))
    per_user_down=$((delta_down / enabled_count))
    per_user_total=$((per_user_up + per_user_down))
    jq --argjson du "$per_user_up" --argjson dd "$per_user_down" --argjson dt "$per_user_total" \
       '.users |= [.[] | if (.status=="active" and .plan_id > 0) then
         .traffic_up_bytes += $du |
         .traffic_down_bytes += $dd |
         .traffic_used_bytes += $dt
       else . end]' "$USERS_DB" > /tmp/vpnm_cron.json && mv /tmp/vpnm_cron.json "$USERS_DB"
fi

# 检查过期和超额
now=$(date +%s)
needs_sync=false

# 标记过期用户
expired_ids=$(jq -r ".users[] | select(.status==\"active\" and .plan_id > 0 and .expires_at < $now) | .id" "$USERS_DB")
for uid in $expired_ids; do
    jq "(.users[] | select(.id==\"$uid\")).status = \"expired\"" "$USERS_DB" > /tmp/vpnm_cron.json && mv /tmp/vpnm_cron.json "$USERS_DB"
    rm -f "$MANAGER_DIR/subs/$(jq -r ".users[] | select(.id==\"$uid\") | .token" "$USERS_DB").txt"
    needs_sync=true
done

# 标记超额用户
over_ids=$(jq -r '.users[] | select(.status=="active" and .plan_id > 0 and .traffic_limit_bytes > 0 and .traffic_used_bytes >= .traffic_limit_bytes) | .id' "$USERS_DB")
for uid in $over_ids; do
    jq "(.users[] | select(.id==\"$uid\")).status = \"overlimit\"" "$USERS_DB" > /tmp/vpnm_cron.json && mv /tmp/vpnm_cron.json "$USERS_DB"
    rm -f "$MANAGER_DIR/subs/$(jq -r ".users[] | select(.id==\"$uid\") | .token" "$USERS_DB").txt"
    needs_sync=true
done

# 需要同步时重建 sing-box 配置
if $needs_sync; then
    SCRIPT_PATH=$(command -v vpn-manager 2>/dev/null || echo "/usr/bin/vpn-manager")
    [ -f "$SCRIPT_PATH" ] && bash "$SCRIPT_PATH" --sync
fi
EOFCRON
    chmod +x "$MANAGER_DIR/traffic_check.sh"

    # 添加 cron 任务（每5分钟）
    local cron_line="*/5 * * * * root bash $MANAGER_DIR/traffic_check.sh >/dev/null 2>&1"
    if ! grep -q "traffic_check.sh" /etc/crontab 2>/dev/null; then
        echo "$cron_line" >> /etc/crontab
        green "流量检查定时任务已设置 (每5分钟)"
    fi
}

check_traffic_manual(){
    yellow "正在检查流量和过期状态..."
    bash "$MANAGER_DIR/traffic_check.sh" 2>/dev/null
    green "检查完成"
    list_users
}

# ======================== 批量操作 ========================
batch_add(){
    show_plans
    echo
    readp "选择套餐 [1-3]: " plan_id
    local plan=$(jq ".plans[] | select(.id == ${plan_id:-0})" "$PLANS_DB" 2>/dev/null)
    [[ -z "$plan" ]] && red "无效套餐" && return

    readp "批量创建数量: " count
    [[ ! "$count" =~ ^[0-9]+$ ]] || [[ "$count" -lt 1 ]] && red "无效数量" && return

    local duration_hours=$(echo "$plan" | jq -r '.duration_hours')
    local traffic_gb=$(echo "$plan" | jq -r '.traffic_gb')
    local now=$(now_ts)
    local expires=$((now + duration_hours * 3600))
    local traffic_limit=$((traffic_gb * 1073741824))
    local sub_port=$(jq -r '.sub_port' "$CONFIG_FILE")
    local server_ip=$(cat "$SB_DIR/server_ipcl.log" 2>/dev/null)

    echo
    green "批量创建 $count 个用户..."
    echo "────────────────────────────────────────────────────"
    printf "%-12s %-36s %s\n" "用户ID" "UUID" "订阅链接"
    echo "────────────────────────────────────────────────────"

    for ((i=1; i<=count; i++)); do
        local uuid=$(gen_uuid)
        local token=$(gen_token)
        local user_id="u_$(openssl rand -hex 4)"
        local remark="批量用户${i}"

        jq --arg id "$user_id" --arg uuid "$uuid" --arg token "$token" \
           --argjson plan_id "$plan_id" --arg remark "$remark" \
           --argjson created "$now" --argjson expires "$expires" \
           --argjson limit "$traffic_limit" \
           '.users += [{
             "id": $id, "uuid": $uuid, "token": $token,
             "plan_id": $plan_id, "remark": $remark,
             "created_at": $created, "expires_at": $expires,
             "traffic_limit_bytes": $limit,
             "traffic_up_bytes": 0, "traffic_down_bytes": 0, "traffic_used_bytes": 0,
             "status": "active"
           }]' "$USERS_DB" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$USERS_DB"

        printf "%-12s %-36s http://%s:%s/sub/%s\n" "$user_id" "$uuid" "$server_ip" "$sub_port" "$token"
    done
    echo "────────────────────────────────────────────────────"

    # 统一同步和生成订阅
    sync_users_to_sb
    gen_all_subs
    green "批量创建完成！"
}

# ======================== 发卡平台集成 ========================
generate_cards(){
    show_plans
    echo
    readp "选择套餐 [1-3]: " plan_id
    local plan=$(jq ".plans[] | select(.id == ${plan_id:-0})" "$PLANS_DB" 2>/dev/null)
    [[ -z "$plan" ]] && red "无效套餐" && return

    readp "生成数量: " count
    [[ ! "$count" =~ ^[0-9]+$ ]] || [[ "$count" -lt 1 ]] && red "无效数量" && return

    local plan_name=$(echo "$plan" | jq -r '.name')
    local duration_hours=$(echo "$plan" | jq -r '.duration_hours')
    local traffic_gb=$(echo "$plan" | jq -r '.traffic_gb')
    local now=$(now_ts)
    local expires=$((now + duration_hours * 3600))
    local traffic_limit=$((traffic_gb * 1073741824))
    local sub_port=$(jq -r '.sub_port' "$CONFIG_FILE")
    local server_ip=$(cat "$SB_DIR/server_ipcl.log" 2>/dev/null)

    # 输出文件：每行一个订阅链接，直接导入发卡平台库存
    local card_file="$MANAGER_DIR/cards_plan${plan_id}_$(date +%Y%m%d_%H%M%S).txt"

    green "正在生成 $count 张 [$plan_name] 卡密..."

    for ((i=1; i<=count; i++)); do
        local uuid=$(gen_uuid)
        local token=$(gen_token)
        local user_id="card_$(openssl rand -hex 4)"
        local remark="卡密${plan_id}-${i}"

        jq --arg id "$user_id" --arg uuid "$uuid" --arg token "$token" \
           --argjson plan_id "$plan_id" --arg remark "$remark" \
           --argjson created "$now" --argjson expires "$expires" \
           --argjson limit "$traffic_limit" \
           '.users += [{
             "id": $id, "uuid": $uuid, "token": $token,
             "plan_id": $plan_id, "remark": $remark,
             "created_at": $created, "expires_at": $expires,
             "traffic_limit_bytes": $limit,
             "traffic_up_bytes": 0, "traffic_down_bytes": 0, "traffic_used_bytes": 0,
             "status": "active"
           }]' "$USERS_DB" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$USERS_DB"

        echo "http://${server_ip}:${sub_port}/sub/${token}" >> "$card_file"
    done

    # 同步和生成订阅
    sync_users_to_sb
    gen_all_subs

    echo
    green "=========================================="
    green "  卡密生成完成！"
    green "=========================================="
    echo "套餐:     $plan_name"
    echo "数量:     $count 张"
    echo "带宽:     $(echo "$plan" | jq -r '.bandwidth_mbps')Mbps"
    echo "流量:     ${traffic_gb}GB"
    echo "有效期:   ${duration_hours}小时"
    echo
    yellow "卡密文件 (每行一个订阅链接，可直接导入发卡平台):"
    echo "$card_file"
    echo
    yellow "内容预览:"
    head -5 "$card_file"
    [[ "$count" -gt 5 ]] && echo "... (共 $count 行)"
    echo
    yellow "提示: 将此文件内容导入到发卡平台(独角数卡/发卡网等)的库存中即可自动发卡"
}

setup_api_secret(){
    local current=$(jq -r '.api_secret' "$CONFIG_FILE")
    if [[ -z "$current" ]] || [[ "$current" == "null" ]]; then
        yellow "当前未设置 API 密钥"
    else
        yellow "当前 API 密钥: $current"
    fi
    echo
    yellow "API 密钥用于发卡平台 webhook 自动创建用户"
    yellow "发卡平台回调地址格式: POST http://服务器IP:订阅端口/api/create"
    readp "输入新的 API 密钥 (回车自动生成): " new_secret
    if [[ -z "$new_secret" ]]; then
        new_secret=$(openssl rand -hex 20)
    fi
    jq --arg s "$new_secret" '.api_secret = $s' "$CONFIG_FILE" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$CONFIG_FILE"
    # 重新创建订阅服务器以包含新密钥
    create_sub_server
    stop_sub_server
    start_sub_server

    echo
    green "API 密钥已设置: $new_secret"
    echo
    local sub_port=$(jq -r '.sub_port' "$CONFIG_FILE")
    local server_ip=$(cat "$SB_DIR/server_ipcl.log" 2>/dev/null)
    yellow "发卡平台 Webhook 配置:"
    echo "  回调地址: http://${server_ip}:${sub_port}/api/create"
    echo "  请求方式: POST"
    echo "  请求参数 (JSON):"
    echo "    {\"secret\": \"${new_secret}\", \"plan_id\": 1}"
    echo "  返回格式:"
    echo "    {\"success\": true, \"sub_url\": \"http://...\", \"expires\": \"...\"}"
    echo
    yellow "plan_id: 1=单日套餐, 2=单月订阅, 3=单月会员升级版"
}

protocol_menu(){
    echo
    local cur=$(jq -r '.protocols[]? // empty' "$CONFIG_FILE" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
    [[ -z "$cur" ]] && cur="vless-reality"
    white "── 订阅协议管理 ──"
    echo "当前启用的协议: $cur"
    echo
    echo "1. 仅 VLESS-Reality (推荐，隐蔽性最强)"
    echo "2. VLESS-Reality + VMess-WS"
    echo "3. 全部协议"
    echo "4. 自定义选择"
    echo "0. 返回"
    readp "请选择: " choice
    local new_protos=""
    case "$choice" in
        1) new_protos='["vless-reality"]';;
        2) new_protos='["vless-reality","vmess-ws"]';;
        3) new_protos='["vless-reality","vmess-ws","hysteria2","tuic","anytls"]';;
        4)
            echo "可选协议: vless-reality, vmess-ws, hysteria2, tuic, anytls"
            readp "输入协议 (逗号分隔): " proto_input
            new_protos=$(echo "$proto_input" | tr ',' '\n' | sed 's/^ *//;s/ *$//' | jq -R . | jq -s .)
            ;;
        *) return;;
    esac
    [[ -z "$new_protos" ]] && return
    jq --argjson p "$new_protos" '.protocols = $p' "$CONFIG_FILE" > /tmp/vpnm_tmp.json && mv /tmp/vpnm_tmp.json "$CONFIG_FILE"
    green "协议已更新: $(echo "$new_protos" | jq -r 'join(", ")')"
    yellow "请执行 [14. 刷新所有订阅链接] 使更改生效"
}

card_platform_menu(){
    echo
    white "── 发卡平台集成 ──"
    echo "1. 批量生成卡密 (导入发卡平台库存)"
    echo "2. 设置 Webhook API 密钥 (自动发卡)"
    echo "3. 查看 API 配置信息"
    echo "0. 返回"
    readp "请选择: " choice
    case "$choice" in
        1) generate_cards;;
        2) setup_api_secret;;
        3)
            local sub_port=$(jq -r '.sub_port' "$CONFIG_FILE")
            local server_ip=$(cat "$SB_DIR/server_ipcl.log" 2>/dev/null)
            local api_secret=$(jq -r '.api_secret // "未设置"' "$CONFIG_FILE")
            echo
            green "发卡平台 API 信息:"
            echo "  服务器:   ${server_ip}:${sub_port}"
            echo "  API密钥:  $api_secret"
            echo "  回调地址: http://${server_ip}:${sub_port}/api/create"
            echo
            yellow "方式一: 批量预生成 → 导入发卡平台库存"
            yellow "方式二: Webhook API → 购买时自动创建用户"
            echo
            yellow "Webhook 请求示例:"
            echo "  curl -X POST http://${server_ip}:${sub_port}/api/create \\"
            echo "    -H 'Content-Type: application/json' \\"
            echo "    -d '{\"secret\":\"${api_secret}\",\"plan_id\":1}'"
            ;;
        *) return;;
    esac
}

# ======================== 导出功能 ========================
export_users(){
    local export_file="$MANAGER_DIR/export_$(date +%Y%m%d_%H%M%S).txt"
    local sub_port=$(jq -r '.sub_port' "$CONFIG_FILE")
    local server_ip=$(cat "$SB_DIR/server_ipcl.log" 2>/dev/null)

    echo "# VPN 用户导出 - $(date '+%Y-%m-%d %H:%M:%S')" > "$export_file"
    echo "# 格式: 用户ID | 备注 | 套餐 | 状态 | 过期时间 | 已用/限额 | 订阅链接" >> "$export_file"
    echo "" >> "$export_file"

    jq -r '.users[] | [.id, .remark, (.plan_id|tostring), .status, .expires_at, .traffic_used_bytes, .traffic_limit_bytes, .token] | @tsv' "$USERS_DB" | \
    while IFS=$'\t' read -r id remark plan_id status expires used limit token; do
        local exp_date=$(date -d @"$expires" '+%Y-%m-%d %H:%M' 2>/dev/null || echo "$expires")
        local used_gb=$(echo "scale=2; $used / 1073741824" | bc 2>/dev/null || echo "0")
        local limit_gb="unlimited"
        [[ "$limit" -gt 0 ]] && limit_gb="$(echo "scale=0; $limit / 1073741824" | bc 2>/dev/null)GB"
        echo "$id | $remark | 套餐$plan_id | $status | $exp_date | ${used_gb}GB/${limit_gb} | http://${server_ip}:${sub_port}/sub/${token}" >> "$export_file"
    done

    green "已导出到: $export_file"
    cat "$export_file"
}

# ======================== 主菜单 ========================
main_menu(){
    check_singbox

    # 首次运行自动初始化
    if [[ ! -f "$USERS_DB" ]] || [[ ! -f "$PLANS_DB" ]]; then
        yellow "首次运行，正在初始化..."
        init_manager
    fi

    while true; do
        echo
        green "╔══════════════════════════════════════════╗"
        green "║       VPN 用户订阅管理系统                ║"
        green "╠══════════════════════════════════════════╣"
        echo  "║  1. 添加用户                              ║"
        echo  "║  2. 删除用户                              ║"
        echo  "║  3. 用户列表                              ║"
        echo  "║  4. 查看用户详情 / 订阅链接               ║"
        echo  "║  5. 续费 / 更换套餐                       ║"
        echo  "║  6. 手动设置流量                          ║"
        echo  "║  7. 启用 / 禁用用户                       ║"
        echo  "║  8. 批量添加用户                          ║"
        echo  "║  9. 导出用户列表                          ║"
        echo  "║ ──────────────────────────────────────── ║"
        echo  "║ 10. 套餐配置管理 (流量/带宽/时长)         ║"
        echo  "║ 11. 订阅协议管理                          ║"
        echo  "║ 12. 订阅服务器管理                        ║"
        echo  "║ 13. 发卡平台集成 (批量卡密/Webhook)       ║"
        echo  "║ ──────────────────────────────────────── ║"
        echo  "║ 14. 刷新所有订阅链接                      ║"
        echo  "║ 15. 手动检查流量 & 过期处理               ║"
        echo  "║ 16. 同步用户到 sing-box                   ║"
        echo  "║ 17. 重新初始化                            ║"
        echo  "║  0. 退出                                  ║"
        green "╚══════════════════════════════════════════╝"

        local cur_protos=$(jq -r '.protocols[]? // empty' "$CONFIG_FILE" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
        [[ -z "$cur_protos" ]] && cur_protos="vless-reality"
        yellow "当前协议: $cur_protos | 限速: sing-box route rules (1.11+) / iptables (1.10)"
        readp "请选择 [0-17]: " menu_choice

        case "$menu_choice" in
            1) add_user;;
            2) del_user;;
            3) list_users;;
            4) user_info;;
            5) renew_user;;
            6) set_traffic;;
            7) disable_user;;
            8) batch_add;;
            9) export_users;;
            10) edit_plan;;
            11) protocol_menu;;
            12) sub_menu;;
            13) card_platform_menu;;
            14) gen_all_subs;;
            15) check_traffic_manual;;
            16) sync_users_to_sb;;
            17) init_manager;;
            0) exit 0;;
            *) red "无效选择";;
        esac
    done
}

# ======================== 命令行参数 ========================
case "$1" in
    --sync)
        check_singbox
        sync_users_to_sb
        ;;
    --check)
        bash "$MANAGER_DIR/traffic_check.sh" 2>/dev/null
        ;;
    --gen-subs)
        check_singbox
        gen_all_subs
        ;;
    --init)
        init_manager
        ;;
    *)
        main_menu
        ;;
esac
