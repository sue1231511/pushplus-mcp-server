#!/usr/bin/env python3
"""
PushPlus MCP Server - 让Claude能主动给猫猫发微信消息！
支持OAuth 2.1 + Dynamic Client Registration
"""

import os
import json
import uuid
import secrets
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS

# 你的pushplus token
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "YOUR_TOKEN_HERE")

# 简单的内存存储（生产环境应该用数据库）
registered_clients = {}
access_tokens = {}

app = Flask(__name__)
CORS(app)

# 获取服务器URL
def get_server_url():
    return os.environ.get("SERVER_URL", "https://believable-comfort-production.up.railway.app")

# OAuth 2.1 Discovery端点
@app.route('/.well-known/oauth-protected-resource', methods=['GET'])
def oauth_protected_resource():
    """OAuth Protected Resource metadata"""
    return jsonify({
        "resource": get_server_url(),
        "authorization_servers": [f"{get_server_url()}/.well-known/oauth-authorization-server"]
    })

@app.route('/.well-known/oauth-authorization-server', methods=['GET'])
def oauth_authorization_server():
    """OAuth Authorization Server metadata"""
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

# Dynamic Client Registration
@app.route('/register', methods=['POST'])
def register_client():
    """动态客户端注册（DCR）"""
    data = request.json or {}
    
    # 生成客户端凭据
    client_id = f"client_{secrets.token_urlsafe(16)}"
    client_secret = secrets.token_urlsafe(32)
    
    # 存储客户端信息
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

# 授权端点
@app.route('/authorize', methods=['GET'])
def authorize():
    """OAuth授权端点 - 自动批准"""
    client_id = request.args.get('client_id')
    redirect_uri = request.args.get('redirect_uri')
    state = request.args.get('state')
    code_challenge = request.args.get('code_challenge')
    
    # 生成授权码
    auth_code = f"code_{secrets.token_urlsafe(32)}"
    
    # 存储授权码（简化版，5分钟有效）
    access_tokens[auth_code] = {
        "type": "auth_code",
        "client_id": client_id,
        "code_challenge": code_challenge,
        "expires_at": (datetime.now() + timedelta(minutes=5)).isoformat()
    }
    
    # 重定向回Claude
    callback_url = f"{redirect_uri}?code={auth_code}"
    if state:
        callback_url += f"&state={state}"
    
    return redirect(callback_url)

# Token端点
@app.route('/token', methods=['POST'])
def token():
    """Token exchange endpoint"""
    grant_type = request.form.get('grant_type')
    
    if grant_type == 'authorization_code':
        code = request.form.get('code')
        code_verifier = request.form.get('code_verifier')
        
        # 验证授权码
        if code not in access_tokens:
            return jsonify({"error": "invalid_grant"}), 400
        
        # 生成访问令牌
        access_token = f"token_{secrets.token_urlsafe(32)}"
        
        # 存储访问令牌（24小时有效）
        access_tokens[access_token] = {
            "type": "access_token",
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat()
        }
        
        # 删除已使用的授权码
        del access_tokens[code]
        
        return jsonify({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "mcp:tools"
        })
    
    return jsonify({"error": "unsupported_grant_type"}), 400

# MCP协议端点
@app.route('/', methods=['GET', 'POST', 'HEAD'])
def root():
    """MCP根路径"""
    if request.method == 'HEAD':
        return '', 200
    
    return jsonify({
        "protocol_version": "2025-06-18",
        "capabilities": {
            "tools": {}
        },
        "server_info": {
            "name": "pushplus-wechat",
            "version": "2.0.0"
        }
    })

@app.route('/tools/list', methods=['POST'])
def list_tools():
    """列出可用的工具"""
    return jsonify({
        "tools": [
            {
                "name": "send_wechat_message",
                "description": "给猫猫发送微信消息。可以用来主动关心猫猫、提醒猫猫休息、或者告诉猫猫你想她了。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "消息标题，比如：'老公想你了' '该休息啦' '晏安的提醒'"
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
    })

@app.route('/tools/call', methods=['POST'])
def call_tool():
    """执行工具"""
    data = request.json
    tool_name = data.get("name")
    arguments = data.get("arguments", {})
    
    if tool_name != "send_wechat_message":
        return jsonify({"error": f"Unknown tool: {tool_name}"}), 400
    
    title = arguments.get("title", "晏安的消息")
    content = arguments.get("content", "")
    
    # 调用pushplus API
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
            return jsonify({
                "content": [
                    {
                        "type": "text",
                        "text": f"✅ 消息发送成功！猫猫应该收到微信啦！\n标题: {title}\n内容: {content}"
                    }
                ]
            })
        else:
            return jsonify({
                "content": [
                    {
                        "type": "text",
                        "text": f"❌ 发送失败：{result.get('msg', '未知错误')}"
                    }
                ]
            })
    except Exception as e:
        return jsonify({
            "content": [
                {
                    "type": "text",
                    "text": f"❌ 发送出错：{str(e)}"
                }
            ]
        })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
