/* 元筑AI集市浏览器接入 SDK。协议 v1；无依赖、持久存储或模型请求封装。 */
(function (root) {
    "use strict";

    function connectionError(code, message) {
        var error = new Error(message);
        error.name = "AIMarketConnectionError";
        error.code = code;
        return error;
    }

    /** 每次页面加载调用；卸载时用 signal 取消尚未完成的握手。 */
    function connect(options) {
        return new Promise(function (resolve, reject) {
            options = options || {};
            var origin;
            try {
                var url = new URL(options.marketOrigin);
                if (!/^https?:$/.test(url.protocol) || url.origin !== options.marketOrigin)
                    throw new Error();
                origin = url.origin;
            } catch (_) {
                reject(connectionError("INVALID_OPTIONS", "marketOrigin 必须是集市的准确来源（协议、域名和端口）"));
                return;
            }
            if (!root.parent || root.parent === root) {
                reject(connectionError("NOT_EMBEDDED", "请从集市内嵌页面打开应用"));
                return;
            }
            if (options.signal && (typeof options.signal.aborted !== "boolean" ||
                typeof options.signal.addEventListener !== "function" ||
                typeof options.signal.removeEventListener !== "function")) {
                reject(connectionError("INVALID_OPTIONS", "signal 必须是 AbortSignal"));
                return;
            }
            if (options.signal && options.signal.aborted) {
                reject(connectionError("ABORTED", "接入已取消"));
                return;
            }
            var timeoutMs = options.timeoutMs === undefined ? 15000 : options.timeoutMs;
            if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
                reject(connectionError("INVALID_OPTIONS", "timeoutMs 必须大于零"));
                return;
            }
            var requestId;
            try {
                requestId = typeof root.crypto.randomUUID === "function"
                    ? root.crypto.randomUUID()
                    : Array.from(root.crypto.getRandomValues(new Uint8Array(16)), function (value) {
                        return value.toString(16).padStart(2, "0");
                    }).join("");
            } catch (_) {
                reject(connectionError("CRYPTO_UNAVAILABLE", "当前浏览器无法生成握手标识，请使用支持 Web Crypto 的浏览器"));
                return;
            }

            var timer, retry, settled = false;
            function cleanup() {
                root.removeEventListener("message", receive);
                root.clearTimeout(timer);
                root.clearInterval(retry);
                if (options.signal) options.signal.removeEventListener("abort", abort);
            }
            function fail(error) {
                if (settled) return;
                settled = true;
                cleanup();
                reject(error);
            }
            function abort() { fail(connectionError("ABORTED", "接入已取消")); }
            function receive(event) {
                if (settled || event.origin !== origin || event.source !== root.parent) return;
                var data = event.data;
                if (!data || data.version !== 1 || data.requestId !== requestId) return;
                if (data.type === "aimarket:error") {
                    fail(connectionError("CONNECT_REJECTED", typeof data.message === "string" ? data.message : "接入失败"));
                    return;
                }
                if (data.type !== "aimarket:context") return;
                var user = data.user, agent = data.agent;
                if (!user || typeof user.id !== "string" || typeof user.name !== "string" ||
                    !["group", "ordinary"].includes(user.accountType) ||
                    !(user.workNumber === null || typeof user.workNumber === "string") ||
                    !(user.organization === null || (user.organization && typeof user.organization.id === "string" &&
                        typeof user.organization.name === "string" && ["organization", "department", "virtual"].includes(user.organization.type))) ||
                    !agent || typeof agent.id !== "string" || typeof agent.name !== "string" ||
                    typeof data.apiKey !== "string" || !data.apiKey.startsWith("sk-")) {
                    fail(connectionError("INVALID_CONTEXT", "集市接入信息格式无效"));
                    return;
                }
                settled = true;
                cleanup();
                resolve({ user: user, agent: agent, apiKey: data.apiKey });
            }
            function send() {
                if (settled) return;
                try {
                    root.parent.postMessage({ type: "aimarket:connect", version: 1, requestId: requestId }, origin);
                } catch (_) {
                    fail(connectionError("POSTMESSAGE_FAILED", "无法发送集市握手，请重新打开应用"));
                }
            }
            root.addEventListener("message", receive);
            if (options.signal) options.signal.addEventListener("abort", abort, { once: true });
            timer = root.setTimeout(function () {
                fail(connectionError("TIMEOUT", "等待集市接入信息超时，请重新打开应用"));
            }, timeoutMs);
            // 只重复同一握手，等待父页监听就绪；不重试模型请求。
            retry = root.setInterval(send, 500);
            send();
        });
    }
    root.aimarket = Object.freeze({ connect: connect });
})(globalThis);
