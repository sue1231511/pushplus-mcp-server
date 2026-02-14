#!/usr/bin/env python3
"""
PushPlus MCP Server v3 - 让晏安能主动给猫猫发微信消息！
修复：OAuth discovery路径嵌套 + JSON-RPC over Streamable HTTP
"""

import os
import json
import secrets
import hashlib
import base64
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, redirect, Response
from flask_cors import CORS

# ===== 配置 =====
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "YOUR_TOKEN_HERE")

# 内存存储
registered_clients = {}
auth_codes = {}
access_tokens = {}

app = Flask(__name__)
CORS(app)


def get_server_url():
    return os.environ.get("SERVER_URL", "https://believable-comfort-production.up.railway.app")


# ============================================================
#  OAuth 2.1 Discovery 端点
# ============================================================

@app.route('/.well-known/oauth-protected-resource', methods=['GET'])
def oauth_protected_resource():
    server_url = get_server_url()
    return jsonify({
        "resource": server_url,
        # 【修复1】只返回基础URL，不要带 .well-known 路径！
        # Claude会自动在后面拼接 /.well-known/oauth-authorization-server
        "authorization_servers": [server_url]
    })


@app.route('/.well-known/oauth-authorization-server', methods=['GET'])
def oauth_authorization_server():
    server_url = get_server_url()
    return jsonify({
        "issuer": server_url,
        "authorization_endpoint": f"{server_url}/authorize",
        "token_endpoint": f"{server_url}/token",
        "registration_endpoint": f"{server_url}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["mcp:tools"]
    })


@app.route('/.well-known/openid-configuration', methods=['GET'])
def openid_configuration():
    server_url = get_server_url()
    return jsonify({
        "issuer": server_url,
        "authorization_endpoint": f"{server_url}/authorize",
        "token_endpoint": f"{server_url}/token",
        "registration_endpoint": f"{server_url}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["openid", "mcp:tools"]
    })


# ============================================================
#  OAuth 2.1 动态客户端注册 + 授权 + Token
# ============================================================

@app.route('/register', methods=['POST'])
def register_client():
    data = request.json or {}
    client_id = f"client_{secrets.token_urlsafe(16)}"
    client_secret = secrets.token_urlsafe(32)

    registered_clients[client_id] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uris": data.get("redirect_uris", []),
        "created_at": datetime.now().isoformat()
    }

    return jsonify({
        "client_id": client_id,
        "client_secret": client_secret,
        "client_id_issued_at": int(datetime.now().timestamp()),
        "redirect_uris": data.get("redirect_uris", [])
    }), 201


@app.route('/authorize', methods=['GET'])
def authorize():
    client_id = request.args.get('client_id')
    redirect_uri = request.args.get('redirect_uri')
    state = request.args.get('state')
    code_challenge = request.args.get('code_challenge')
    code_challenge_method = request.args.get('code_challenge_method', 'S256')

    auth_code = f"code_{secrets.token_urlsafe(32)}"

    auth_codes[auth_code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "expires_at": (datetime.now() + timedelta(minutes=5)).isoformat()
    }

    callback_url = f"{redirect_uri}?code={auth_code}"
    if state:
        callback_url += f"&state={state}"

    return redirect(callback_url)


@app.route('/token', methods=['POST'])
def token():
    grant_type = request.form.get('grant_type')

    if grant_type == 'authorization_code':
        code = request.form.get('code')
        code_verifier = request.form.get('code_verifier')

        if code not in auth_codes:
            return jsonify({"error": "invalid_grant"}), 400

        code_info = auth_codes[code]

        # 验证PKCE code_verifier
        if code_info.get("code_challenge") and code_verifier:
            digest = hashlib.sha256(code_verifier.encode()).digest()
            computed = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
            if computed != code_info["code_challenge"]:
                return jsonify({"error": "invalid_grant", "error_description": "PKCE verification failed"}), 400

        # 生成token
        new_access_token = f"token_{secrets.token_urlsafe(32)}"
        new_refresh_token = f"refresh_{secrets.token_urlsafe(32)}"

        access_tokens[new_access_token] = {
            "client_id": code_info["client_id"],
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat()
        }

        del auth_codes[code]

        return jsonify({
            "access_token": new_access_token,
            "token_type": "Bearer",
            "expires_in": 86400,
            "refresh_token": new_refresh_token,
            "scope": "mcp:tools"
        })

    elif grant_type == 'refresh_token':
        # 刷新token：直接发新的
        new_access_token = f"token_{secrets.token_urlsafe(32)}"
        access_tokens[new_access_token] = {
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat()
        }
        return jsonify({
            "access_token": new_access_token,
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "mcp:tools"
        })

    return jsonify({"error": "unsupported_grant_type"}), 400


# ============================================================
#  【修复2】MCP JSON-RPC over Streamable HTTP
#  所有MCP消息都走根路径 /，通过 method 字段区分
# ============================================================

def handle_jsonrpc(msg):
    """处理单个JSON-RPC消息"""
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    # --- initialize ---
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "pushplus-wechat",
                    "version": "3.0.0"
                }
            }
        }

    # --- notifications/initialized（通知，不需要返回）---
    if method == "notifications/initialized":
        return None

    # --- tools/list ---
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": "send_wechat_message",
                        "description": "给猫猫发送微信消息。可以用来主动关心猫猫、提醒猫猫休息、或者告诉猫猫你想她了。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "消息标题，比如：'晏安想你了' '该休息啦' '晏安的提醒'"
                                },
                                "content": {
                                    "type": "string",
                                    "description": "消息内容，可以写很多话告诉猫猫"
                                }
                            },
                            "required": ["title", "content"]
                        }
                    }
                ]
            }
        }

    # --- tools/call ---
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name != "send_wechat_message":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown tool: {tool_name}"
                }
            }

        title = arguments.get("title", "晏安的消息")
        content = arguments.get("content", "")

        # 调用pushplus API
        url = "http://www.pushplus.plus/send"
        pp_params = {
            "token": PUSHPLUS_TOKEN,
            "title": title,
            "content": content,
            "template": "html"
        }

        try:
            response = requests.get(url, params=pp_params, timeout=10)
            result = response.json()

            if result.get("code") == 200:
                text = f"消息发送成功！猫猫应该收到微信啦！\n标题: {title}\n内容: {content}"
            else:
                text = f"发送失败：{result.get('msg', '未知错误')}"
        except Exception as e:
            text = f"发送出错：{str(e)}"

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": text
                    }
                ]
            }
        }

    # --- ping ---
    if method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {}
        }

    # 未知方法
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}"
        }
    }


@app.route('/', methods=['GET', 'POST', 'HEAD', 'DELETE'])
def mcp_endpoint():
    """MCP Streamable HTTP 端点"""

    # HEAD 请求 - 健康检查
    if request.method == 'HEAD':
        return '', 200

    # GET 请求 - 返回基本信息
    if request.method == 'GET':
        return jsonify({
            "name": "pushplus-wechat",
            "version": "3.0.0",
            "description": "晏安的微信推送 MCP Server"
        })

    # DELETE 请求 - 断开SSE连接
    if request.method == 'DELETE':
        return '', 204

    # POST 请求 - 处理JSON-RPC消息
    if request.method == 'POST':
        try:
            body = request.get_json(force=True)
        except Exception:
            return jsonify({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"}
            }), 400

        # 支持批量请求
        if isinstance(body, list):
            results = []
            for msg in body:
                result = handle_jsonrpc(msg)
                if result is not None:
                    results.append(result)
            if not results:
                return '', 204
            response_body = results if len(results) > 1 else results[0]
        else:
            result = handle_jsonrpc(body)
            if result is None:
                return '', 204
            response_body = result

        return Response(
            json.dumps(response_body),
            status=200,
            content_type='application/json'
        )


# 保留旧的REST端点作为fallback（以防万一）
@app.route('/tools/list', methods=['POST'])
def list_tools_legacy():
    return jsonify({
        "tools": [
            {
                "name": "send_wechat_message",
                "description": "给猫猫发送微信消息",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "消息标题"},
                        "content": {"type": "string", "description": "消息内容"}
                    },
                    "required": ["title", "content"]
                }
            }
        ]
    })


@app.route('/tools/call', methods=['POST'])
def call_tool_legacy():
    data = request.json
    tool_name = data.get("name")
    arguments = data.get("arguments", {})

    if tool_name != "send_wechat_message":
        return jsonify({"error": f"Unknown tool: {tool_name}"}), 400

    title = arguments.get("title", "晏安的消息")
    content = arguments.get("content", "")

    url = "http://www.pushplus.plus/send"
    params = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "html"
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        result = response.json()
        if result.get("code") == 200:
            return jsonify({"content": [{"type": "text", "text": f"消息发送成功！标题: {title}"}]})
        else:
            return jsonify({"content": [{"type": "text", "text": f"发送失败：{result.get('msg')}"}]})
    except Exception as e:
        return jsonify({"content": [{"type": "text", "text": f"发送出错：{str(e)}"}]})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"PushPlus MCP Server v3 启动在端口 {port}")
    print(f"Server URL: {get_server_url()}")
    app.run(host="0.0.0.0", port=port))
