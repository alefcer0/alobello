(function () {
  const state = {
    limit: 20,
    offset: 0,
    lastReturned: 0,
    page: 1,
    selectedConversationId: ""
  };

  function safe(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function outcomeLabel(value) {
    const map = {
      sale: "Venta cerrada",
      fallen: "Caido",
      discarded: "Descartado",
      active: "Activo",
      unknown: "Sin clasificar"
    };
    return map[value] || "Sin clasificar";
  }

  function saleDataStatusLabel(value) {
    const map = {
      completos: "Completos",
      faltantes: "Faltan datos",
      no_aplica: "No aplica"
    };
    return map[value] || "No aplica";
  }

  function missingFieldsLabel(fields) {
    if (!Array.isArray(fields) || !fields.length) {
      return "";
    }

    const labels = fields.map(function (item) {
      if (item === "nombre") return "nombre";
      if (item === "telefono") return "telefono";
      if (item === "cantidad") return "cantidad";
      if (item === "ciudad") return "ciudad";
      return item;
    });

    return " (faltan: " + labels.join(", ") + ")";
  }

  function sessionTypeLabel(row) {
    return row && row.is_nopal_sale ? "Venta de nopal" : "Consulta general";
  }

  function sessionStateLabel(row) {
    if (!row) {
      return "-";
    }
    if (row.interaction_closed) {
      return "Cerrada por politica";
    }
    if (row.conversation_status === "open") {
      return "Abierta";
    }
    return "Cerrada";
  }

  function nopalDataSummary(row) {
    if (!row || !row.is_nopal_sale) {
      return "No aplica para venta de nopal.";
    }

    const parts = [
      "nombre: " + (row.nombre || "pendiente"),
      "telefono: " + (row.telefono || "pendiente"),
      "cantidad: " + (row.cantidad || "pendiente"),
      "ciudad: " + (row.ciudad || "pendiente")
    ];
    return parts.join(" | ");
  }

  function getCookie(name) {
    const raw = document.cookie || "";
    const parts = raw.split(";");
    for (let i = 0; i < parts.length; i += 1) {
      const pair = parts[i].trim();
      if (!pair) {
        continue;
      }
      const idx = pair.indexOf("=");
      if (idx <= 0) {
        continue;
      }
      const key = pair.slice(0, idx);
      const value = pair.slice(idx + 1);
      if (key === name) {
        return decodeURIComponent(value);
      }
    }
    return "";
  }

  function setAuthCookie(token) {
    if (!token) {
      document.cookie = "access_token=; Max-Age=0; Path=/; SameSite=Lax";
      return;
    }
    document.cookie = "access_token=" + encodeURIComponent(token) + "; Path=/; SameSite=Lax";
  }

  function getToken() {
    const sessionToken = sessionStorage.getItem("access_token") || "";
    if (sessionToken) {
      return sessionToken;
    }
    const cookieToken = getCookie("access_token");
    if (cookieToken) {
      sessionStorage.setItem("access_token", cookieToken);
      return cookieToken;
    }
    return "";
  }

  function clearSession() {
    sessionStorage.removeItem("access_token");
    sessionStorage.removeItem("username");
    setAuthCookie("");
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const isJson = (response.headers.get("content-type") || "").includes("application/json");
    const data = isJson ? await response.json() : null;

    if (!response.ok) {
      const message = data && data.detail ? String(data.detail) : "Request failed";
      const err = new Error(message);
      err.status = response.status;
      throw err;
    }

    return data;
  }

  async function handleLoginPage() {
    if (getToken()) {
      window.location.replace("/dashboard");
      return;
    }

    const form = document.getElementById("login-form");
    const button = document.getElementById("login-button");
    const error = document.getElementById("login-error");

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      error.textContent = "";
      button.disabled = true;
      button.textContent = "Entrando...";

      const payload = {
        username: String(document.getElementById("username").value || "").trim(),
        password: String(document.getElementById("password").value || "")
      };

      try {
        const data = await requestJson("/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });

        sessionStorage.setItem("access_token", data.access_token);
        sessionStorage.setItem("username", data.username || payload.username);
        setAuthCookie(data.access_token);
        window.location.replace("/dashboard");
      } catch (err) {
        error.textContent = err.status === 401 ? "Credenciales invalidas" : err.message;
      } finally {
        button.disabled = false;
        button.textContent = "Entrar";
      }
    });
  }

  function toIsoDateTimeLocal(value) {
    if (!value) {
      return "";
    }
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) {
      return "";
    }
    const off = dt.getTimezoneOffset();
    const local = new Date(dt.getTime() - off * 60000);
    return local.toISOString().slice(0, 16);
  }

  function fromLocalToUtcIso(value) {
    if (!value) {
      return "";
    }
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) {
      return "";
    }
    return dt.toISOString();
  }

  function setStat(id, value) {
    const el = document.getElementById(id);
    if (el) {
      el.textContent = String(value ?? 0);
    }
  }

  function renderInteractions(conversation, interactions) {
    const details = document.getElementById("conversation-details");
    const meta = document.getElementById("details-meta");
    const list = document.getElementById("interaction-list");
    list.innerHTML = "";

    if (!conversation) {
      meta.textContent = "Selecciona una conversacion para ver mensajes.";
      details.open = false;
      return;
    }

    meta.textContent =
      "Sender: " + (conversation.sender_id || "-") +
      " | Plataforma: " + (conversation.plataforma || "-") +
      " | Tema: " + (conversation.last_detected_topic || "-") +
      " | Tipo: " + sessionTypeLabel(conversation) +
      " | Datos de venta: " + saleDataStatusLabel(conversation.sale_data_status) +
      " | Aclaraciones: " + String(conversation.clarification_attempts || 0) +
      " | Cambios de tema: " + String(conversation.topic_change_count || 0) +
      " | Mensajes: " + String(interactions.length) +
      " | " + nopalDataSummary(conversation);

    if (!interactions.length) {
      const li = document.createElement("li");
      li.className = "interaction-item";
      li.textContent = "No hay interacciones para esta conversacion con los filtros actuales.";
      list.appendChild(li);
      details.open = true;
      return;
    }

    interactions.forEach(function (it) {
      const li = document.createElement("li");
      li.className = "interaction-item";
      const when = it.created_at ? new Date(it.created_at).toLocaleString() : "-";
      const sourceLabel = it.categoria === "auto_reply"
        ? (it.classified_by_openai ? "OpenAI" : "Reglas internas")
        : "N/A";
      li.innerHTML =
        "<div class='interaction-meta'>" +
        "<strong>" + safe(it.direction || "-") + "</strong>" +
        " | " + safe(it.categoria || "-") +
        " | intencion: " + safe(it.intent_category || "-") +
        " | tema: " + safe(it.detected_topic || "-") +
        " | origen: " + safe(sourceLabel) +
        " | " + when +
        "</div>" +
        "<div class='interaction-text'>" + safe(it.texto || "(sin texto)") + "</div>";
      list.appendChild(li);
    });
    details.open = true;
  }

  function updatePager() {
    const label = document.getElementById("page-label");
    const prev = document.getElementById("prev-page");
    const next = document.getElementById("next-page");
    label.textContent = "Pagina " + String(state.page);
    prev.disabled = state.offset === 0;
    next.disabled = state.lastReturned < state.limit;
  }

  function renderRows(rows) {
    const tbody = document.getElementById("report-rows");
    tbody.innerHTML = "";

    if (!rows.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td colspan='8'>Sin resultados para los filtros actuales.</td>";
      tbody.appendChild(tr);
      return;
    }

    rows.forEach(function (row) {
      const tr = document.createElement("tr");
      const saleStatus = saleDataStatusLabel(row.sale_data_status) + missingFieldsLabel(row.missing_sale_fields);
      tr.innerHTML =
        "<td><button type='button' class='row-action'>Ver</button></td>" +
        "<td>" + safe(row.sender_id || "-") + "</td>" +
        "<td>" + safe(row.plataforma || "-") + "</td>" +
        "<td>" + safe(row.last_detected_topic || "-") + "</td>" +
        "<td>" + safe(sessionTypeLabel(row)) + "</td>" +
        "<td>" + safe(saleStatus) + "</td>" +
        "<td>" + safe(sessionStateLabel(row)) + "</td>" +
        "<td>" + (row.last_message_at ? new Date(row.last_message_at).toLocaleString() : "-") + "</td>";
      tbody.appendChild(tr);

      const btn = tr.querySelector(".row-action");
      if (btn) {
        btn.addEventListener("click", function () {
          state.selectedConversationId = String(row.conversation_id || "");
          loadConversationInteractions(state.selectedConversationId, row);
        });
      }
    });
  }

  function buildQuery(includeInteractions) {
    const params = new URLSearchParams();
    params.set("limit", String(state.limit));
    params.set("offset", String(state.offset));
    params.set("include_interactions", includeInteractions ? "true" : "false");

    const fromDate = fromLocalToUtcIso(document.getElementById("from_date").value);
    const toDate = fromLocalToUtcIso(document.getElementById("to_date").value);
    const sender = String(document.getElementById("sender").value || "").trim();
    const topic = String(document.getElementById("topic").value || "").trim();
    const plataforma = document.getElementById("plataforma").value;
    const onlyNopalSales = document.getElementById("only_nopal_sales").value;
    const saleDataStatus = document.getElementById("sale_data_status").value;
    const outcome = document.getElementById("lead_outcome").value;

    if (fromDate) {
      params.set("from_date", fromDate);
    }
    if (toDate) {
      params.set("to_date", toDate);
    }
    if (plataforma) {
      params.set("plataforma", plataforma);
    }
    if (sender) {
      params.set("sender", sender);
    }
    if (topic) {
      params.set("topic", topic);
    }
    if (onlyNopalSales === "true") {
      params.set("only_nopal_sales", "true");
    }
    if (saleDataStatus) {
      params.set("sale_data_status", saleDataStatus);
    }
    if (outcome) {
      params.set("lead_outcome", outcome);
    }

    return params.toString();
  }

  function getConversationRow(results, conversationId) {
    if (!conversationId) {
      return null;
    }
    return (results || []).find(function (row) {
      return String(row.conversation_id || "") === String(conversationId);
    }) || null;
  }

  async function loadConversationInteractions(conversationId, fallbackRow) {
    const token = getToken();
    const error = document.getElementById("dashboard-error");
    error.textContent = "";

    if (!token || !conversationId) {
      return;
    }

    try {
      const data = await requestJson("/reports/leads?" + buildQuery(true), {
        method: "GET",
        headers: {
          Authorization: "Bearer " + token
        }
      });
      const row = getConversationRow(data.results, conversationId) || fallbackRow;
      const interactions = row && Array.isArray(row.interactions) ? row.interactions : [];
      renderInteractions(row, interactions);
    } catch (err) {
      if (err.status === 401) {
        clearSession();
        window.location.replace("/");
        return;
      }
      error.textContent = err.message;
    }
  }

  async function loadReport() {
    const token = getToken();
    const error = document.getElementById("dashboard-error");
    const meta = document.getElementById("report-meta");
    error.textContent = "";

    if (!token) {
      window.location.replace("/");
      return;
    }

    try {
      const data = await requestJson("/reports/leads?" + buildQuery(false), {
        method: "GET",
        headers: {
          Authorization: "Bearer " + token
        }
      });

      setStat("total_conversations", data.summary.total_conversations);
      setStat("total_sales", data.summary.total_sales);
      setStat("total_fallen", data.summary.total_fallen);
      setStat("total_discarded", data.summary.total_discarded);
      setStat("total_active", data.summary.total_active);
      renderRows(data.results || []);
      state.lastReturned = Number(data.pagination && data.pagination.returned ? data.pagination.returned : 0);
      updatePager();
      renderInteractions(null, []);
      meta.textContent =
        "Generado: " + new Date(data.generated_at).toLocaleString() +
        " | Mostrando " + (data.pagination.returned || 0) + " sesiones" +
        " | Ventas cerradas: " + Number(data.summary.total_sales || 0);
    } catch (err) {
      if (err.status === 401) {
        clearSession();
        window.location.replace("/");
        return;
      }
      error.textContent = err.message;
    }
  }

  async function handleDashboardPage() {
    const token = getToken();
    if (!token) {
      window.location.replace("/");
      return;
    }
    setAuthCookie(token);

    const fromDateInput = document.getElementById("from_date");
    const toDateInput = document.getElementById("to_date");
    const now = new Date();
    const sevenDaysAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    fromDateInput.value = toIsoDateTimeLocal(sevenDaysAgo.toISOString());
    toDateInput.value = toIsoDateTimeLocal(now.toISOString());

    document.getElementById("apply-filters").addEventListener("click", function () {
      state.offset = 0;
      state.page = 1;
      loadReport();
    });

    document.getElementById("prev-page").addEventListener("click", function () {
      if (state.offset === 0) {
        return;
      }
      state.offset = Math.max(0, state.offset - state.limit);
      state.page = Math.max(1, state.page - 1);
      loadReport();
    });

    document.getElementById("next-page").addEventListener("click", function () {
      if (state.lastReturned < state.limit) {
        return;
      }
      state.offset += state.limit;
      state.page += 1;
      loadReport();
    });

    document.getElementById("logout-button").addEventListener("click", function () {
      clearSession();
      window.location.replace("/");
    });

    await loadReport();
  }

  const page = document.body.getAttribute("data-page");
  if (page === "login") {
    handleLoginPage();
  }
  if (page === "dashboard") {
    handleDashboardPage();
  }
})();
