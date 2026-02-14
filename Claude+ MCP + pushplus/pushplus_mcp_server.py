#!/usr/bin/env python3
"""
PushPlus MCP Server - 让Claude能主动给猫猫发微信消息！
HTTP版本 - 支持手机APP和网页版Claude
"""

import os
import json
import requests
from flask import Flask, request, jsonify

# 你的pushplus token（等会要填进去）
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "YOUR_TOKEN_HERE")

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST', 'HEAD'])
def root():
    """MCP根路径 - 返回服务器信息"""
    if request.method == 'HEAD':
        return '', 200
    
    return jsonify({
        "protocol_version": "2025-06-18",
        "capabilities": {
            "tools": {}
        },
        "server_info": {
            "name": "pushplus-wechat",
            "version": "1.0.0"
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
