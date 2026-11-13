"""Safe OpenAI Responses boundary for the CEO's private office.

A timeout may have produced a billable result upstream. Never blindly auto-retry.
Keep user prompts, secrets and provider error bodies out of application logs.
"""
import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request

from fastapi import HTTPException

logger=logging.getLogger("office.ai.provider")
_BASE="https://api.openai.com/v1"
_REQUEST_ID=re.compile(r"^[A-Za-z0-9_-]{4,120}$")
_SAFE_CODES={
    "insufficient_quota","billing_hard_limit_reached","rate_limit_exceeded",
    "invalid_api_key","model_not_found","model_not_supported",
    "invalid_request_error","context_length_exceeded","unsupported_parameter",
    "server_error","invalid_image","image_parse_error","content_policy_violation",
}
_MESSAGES={
    "insufficient_quota":"OpenAI API quota or billing limit reached. Check OpenAI Platform billing before retry.",
    "billing_hard_limit_reached":"OpenAI API spending limit reached. Check OpenAI Platform billing.",
    "rate_limit_exceeded":"OpenAI API rate limit reached. Run fewer jobs concurrently, or check your usage tier.",
    "invalid_api_key":"OpenAI API credential was rejected. Check the OPENAI_API_KEY in Railway.",
    "model_not_found":"Configured OPENAI_MODEL is not available to this API key. Check model access.",
    "model_not_supported":"Configured model does not support this operation.",
    "context_length_exceeded":"AI request exceeds model context limit. Use a shorter brief or fewer references.",
    "unsupported_parameter":"AI model rejected a request parameter. Check model capabilities.",
}
class AIProviderError(HTTPException):
    def __init__(self,status_code,detail,code,provider_status=None,request_id=None,uncertain=False):
        super().__init__(status_code,detail)
        self.code=code
        self.provider_status=provider_status
        self.request_id=request_id
        self.uncertain=uncertain


def _error_code(body):
    try:
        decoded=json.loads(body.decode("utf-8",errors="replace"))
        data=decoded.get("error") if isinstance(decoded,dict) else None
        if not isinstance(data,dict):return "provider_http_error"
        for x in (data.get("code"),data.get("type")):
            if isinstance(x,str) and x in _SAFE_CODES:return x
    except (ValueError,UnicodeError,AttributeError):
        pass
    return "provider_http_error"


def _request_id(value):
    return value if isinstance(value,str) and _REQUEST_ID.fullmatch(value) else None


def _api_call(request,timeout,operation):
    started=time.monotonic()
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            data=json.load(response)
            request_id=_request_id(response.headers.get("x-request-id"))
    except urllib.error.HTTPError as exc:
        code=_error_code(exc.read(4096))
        request_id=_request_id(exc.headers.get("x-request-id")) if exc.headers else None
        message=_MESSAGES.get(code, "OpenAI API request failed (HTTP "+str(exc.code)+"). Check provider status and model access.")
        logger.warning("AI provider HTTP failure op=%s http=%d code=%s request_id=%s seconds=%.1f",
                       operation,exc.code,code,request_id or "-",time.monotonic()-started)
        raise AIProviderError(503 if exc.code in (429,500,502,503,504) else 502,
                              message,code,exc.code,request_id) from None
    except (socket.timeout,TimeoutError) as exc:
        logger.warning("AI provider timed out op=%s seconds=%.1f",operation,time.monotonic()-started)
        raise AIProviderError(504,"AI provider request timed out. The upstream outcome is uncertain; verify before retrying.",
                              "timeout",uncertain=True) from None
    except (urllib.error.URLError,ConnectionError,OSError,ValueError) as exc:
        name=type(exc).__name__
        logger.warning("AI provider connection failure op=%s type=%s seconds=%.1f",
                       operation,name,time.monotonic()-started)
        raise AIProviderError(503,"AI provider could not be reached. Check network and API availability.",
                              "connection_error",uncertain=True) from None
    if not isinstance(data,dict):
        raise AIProviderError(502,"AI provider returned an invalid JSON response.","invalid_json")
    if data.get("status")=="failed":
        detail=data.get("error") or {}
        code=(detail.get("code") if isinstance(detail,dict) else None)
        code=code if code in _SAFE_CODES else "response_failed"
        raise AIProviderError(502,_MESSAGES.get(code,"AI provider marked the response as failed."),
                              code,request_id=request_id)
    return data


def response(payload,operation="workforce",timeout=None):
    if os.getenv("AI_ENGINE_ENABLED","false").lower()!="true":
        raise AIProviderError(503,"AI engine is disabled in Railway.","ai_disabled")
    key=os.getenv("OPENAI_API_KEY","").strip()
    if not key:
        raise AIProviderError(503,"OPENAI_API_KEY is missing from Railway.","key_missing")
    seconds=timeout if timeout is not None else int(os.getenv("AI_WORKER_TIMEOUT_SECONDS","165"))
    seconds=max(10,min(int(seconds),220))
    request=urllib.request.Request(_BASE+"/responses",data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},method="POST")
    return _api_call(request,seconds,operation)


def check_model():
    """Unbilled model metadata check: doesn't guarantee quota for generations."""
    enabled=os.getenv("AI_ENGINE_ENABLED","false").lower()=="true"
    model=os.getenv("OPENAI_MODEL","gpt-5.4").strip()
    if not enabled:return {"connected":False,"status":"disabled","model":model,
                           "advice":"Enable AI_ENGINE_ENABLED in Railway"}
    key=os.getenv("OPENAI_API_KEY","").strip()
    if not key:return {"connected":False,"status":"missing_key","model":model,
                       "advice":"Set OPENAI_API_KEY in Railway"}
    url=_BASE+"/models/"+urllib.parse.quote(model,safe="")
    request=urllib.request.Request(url,headers={"Authorization":"Bearer "+key},method="GET")
    try:
        data=_api_call(request,12,"model_check")
    except AIProviderError as exc:
        return {"connected":False,"status":exc.code,"model":model,
                "http_status":exc.provider_status,
                "advice":exc.detail}
    return {"connected":True,"status":"model_visible","model":model,
            "advice":"API key can access model metadata; test generation separately to confirm billing/quota."}


def text_output(data):
    return "\n".join(
        str(part.get("text","")) for message in data.get("output",[])
        if isinstance(message,dict) and message.get("type")=="message"
        for part in message.get("content",[])
        if isinstance(part,dict) and part.get("type")=="output_text"
    ).strip()
