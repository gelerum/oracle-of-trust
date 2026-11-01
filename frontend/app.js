const $ = (id) => document.getElementById(id);
const state = { sources: [], processes: [], workflows: [], requests: [], statuses: [], caps: null, editing: null, jsonKind: "", jsonId: null, busy: false, loadingCatalog: true };
const pretty = (value) => JSON.stringify(value, null, 2);

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function button(text, action, className = "text-button") {
  const element = node("button", text, className);
  element.type = "button";
  element.addEventListener("click", action);
  return element;
}
function badge(text, style = "muted") { return node("span", text, `badge ${style}`); }
function banner(message, success = false) { $("banner").textContent = message; $("banner").className = success ? "banner success-banner" : "banner"; $("banner").hidden = !message; }
function sourceStatus(source) {
  if (!source.enabled) {const label = badge("Не проверен");label.title = "Источник выключен";return label;}
  if (source.type !== "mock" && source.access?.approved !== true) {const label = badge("Не проверен");label.title = "Нет разрешения на доступ";return label;}
  const health = state.statuses.find((item) => item.sourceId === source.sourceId);
  if (health?.adapterAvailable === false) return badge("Недоступен", "failed");
  return health?.connected === true ? badge("Доступен", "success")
    : health?.connected === false ? badge("Недоступен", "failed") : badge("Не проверен");
}
async function api(path, options = {}) {
  const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...options.headers };
  const response = await fetch(path, { ...options, headers, cache: "no-store", signal: AbortSignal.timeout(20000) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : pretty(data.detail || data));
  return data;
}
function cleanSource(source) { const copy = { ...source }; delete copy.responseSchema; return copy; }
function renderSources() {
  const body = $("source-rows"); body.replaceChildren();
  const search = $("source-search").value.trim().toLowerCase();
  const type = $("source-type").value, status = $("source-state").value;
  const sources = state.sources.filter((source) => (!type || source.type === type)
    && (!status || source.enabled === (status === "enabled"))
    && `${sourceTitle(source)} ${source.sourceId} ${source.factTypes.join(" ")}`.toLowerCase().includes(search));
  for (const source of sources) {
    const row = node("tr"), name = node("td"), transport = node("td"), facts = node("td");
    name.append(button(sourceTitle(source), () => sourceDetail(source), "source-name"));
    if (source.type === "mock") name.append(badge("Mock"));
    transport.append(node("span", source.type.toUpperCase()), node("span", source.adapter, "sub mono"));
    for (const fact of source.factTypes) facts.append(node("span", factTitle(fact), "fact-tag"));
    const health = node("td"); health.append(sourceStatus(source));
    const checked = monitor.health.get(source.sourceId); if (checked) health.append(node("small", "В этой вкладке: " + formatTime(checked), "health-time"));
    const action = node("td");
    const actions = node("div", undefined, "source-actions");
    actions.append(button("Ответы →", () => openResponses({sourceId: source.sourceId})));
    actions.append(button("Настройки", () => editSource(source)));
    action.append(actions);
    row.append(name, facts, health, action); body.append(row);
  }
  $("source-empty").hidden = sources.length !== 0;
  $("metric-total").textContent = state.sources.length;
  $("metric-enabled").textContent = state.sources.filter((source) => source.enabled).length;
  $("metric-connected").textContent = state.statuses.filter((source) => source.connected === true).length;
  $("metric-workflows").textContent = state.workflows.length;
  $("nav-count").textContent = state.sources.length;
}
function renderWorkflows() {
  const container = $("workflow-cards"); container.replaceChildren();
  for (const workflow of state.workflows) {
    const card = node("article", undefined, "profile-card");
    card.append(node("h2", workflow.name));
    const connection = node("dl", undefined, "integration-summary");
    detailEntry(connection, "Сеть", networkName(workflow));
    detailEntry(connection, "Контракт", workflow.verifyingContract.slice(0, 10) + "…" + workflow.verifyingContract.slice(-6));
    detailEntry(connection, "Подпись", "EIP-712");
    card.append(connection);
    const process = state.processes.find((item) => item.processId === workflow.processId);
    card.append(node("p", `Бизнес-процесс: ${process?.name || workflow.processId}`, "description"));
    if (process) card.append(button("Открыть бизнес-условия →", () => showPage("processes")));
    if (process && !process.enabled) card.append(badge("Процесс выключен"));
    const row = node("div", undefined, "row");
    row.append(badge(workflow.enabled ? "Включён" : "Выключен", workflow.enabled ? "success" : "muted"),
      button("Редактировать интеграцию →", () => editJson("workflow", workflow)));
    card.append(row);
    const technical = node("details", undefined, "control-technical"); technical.append(node("summary", "Технические детали"));
    const fields = node("dl", undefined, "detail-grid");
    detailEntry(fields, "Контракт", workflow.verifyingContract);
    detailEntry(fields, "Функции чтения", (workflow.checks || []).map((check) => check.function).join(", ") || "Не заданы");
    detailEntry(fields, "Функция подписанта", workflow.signerFunction || "Не задана");
    detailEntry(fields, "Домен EIP-712", workflow.attestation?.name || "Не задан");
    detailEntry(fields, "Версия домена", workflow.attestation?.version || "Не задана");
    detailEntry(fields, "Адрес оракула", state.caps?.oracleSigner || "Нет данных");
    technical.append(fields); const abi = node("details"); abi.append(node("summary", "ABI"), node("pre", pretty(workflow.abi))); technical.append(abi);
    card.append(technical); container.append(card);
  }
  if (!state.workflows.length) container.append(node("p", "Технические интеграции пока не зарегистрированы.", "empty"));
}
function renderRequests() {
  const body = $("request-rows"), selected = $("history-process").value; body.replaceChildren();
  $("history-process").replaceChildren(node("option", "Все процессы")); $("history-process").firstChild.value = "";
  for (const process of state.processes) {const option = node("option", process.name);option.value = process.processId;$("history-process").append(option);} $("history-process").value = selected;
  for (const request of state.requests) {
    const model = summarize(request), process = requestProcess(request), filter = $("history-status").value;
    if (selected && process?.processId !== selected) continue;
    if (filter && !(filter === "paid" ? model.paid : filter === "signed" ? model.signature === "SIGNED" && !model.paid : filter === "rejected" ? model.evaluation === "REJECTED" && !model.paid : !model.paid && model.signature !== "SIGNED" && model.evaluation !== "REJECTED")) continue;
    const row = node("tr"), check = node("td"), oracle = node("td"), contract = node("td"), action = node("td");
    check.append(badge(model.loading ? "Загрузка…" : model.error ? "Недоступны" : model.total ? `${model.count}/${model.total}` : "Нет данных", statusStyle(model.items.length ? model.evaluation : "NOT_CHECKED")));
    oracle.append(badge(model.signature === "SIGNED" ? "Подписано" : model.signature === "EXPIRED" ? "Подпись истекла" : "Не выдано", statusStyle(model.signature)));
    contract.append(badge(model.paid ? "Выполнено" : model.snapshot?.execution?.status === "NOT_EXECUTED" ? "Не исполнено" : "Не проверен", model.paid ? "success" : "muted"));
    action.append(button("Аудит", () => requestDetail(request)));
    row.append(node("td", requestName(request)), node("td", process?.name || "Нет процесса"), check, oracle, contract, node("td", model.date ? formatTime(model.date) : "Не предоставлена"), action);body.append(row);
  }
  $("request-empty").hidden = body.children.length !== 0;
}
function detailEntry(container, title, value) {
  container.append(node("dt", title), node("dd", value));
}
async function toggleSource(source) {
  try {
    await api(`/sources/${source.sourceId}`, { method: "PUT", body: pretty({ ...source, enabled: !source.enabled }) });
    banner(source.enabled ? "Источник выключен." : "Источник включён.", true);
    await refresh();
    return true;
  } catch (error) {
    banner(error.message);
    return false;
  }
}
async function sourceDetail(source) {
  $("detail-title").textContent = sourceTitle(source);
  const container = $("detail-content"); container.replaceChildren();
  const actions = node("div", undefined, "action-group");
  actions.append(button("Все ответы источника →", () => openResponses({sourceId: source.sourceId})));
  actions.append(button("Редактировать", () => { $("detail-dialog").close(); editSource(source); }, "secondary"),
    button(source.enabled ? "Выключить" : "Включить", async () => {
      if (await toggleSource(source)) $("detail-dialog").close();
    }, "secondary"),
    button("Проверить связь", async () => {
      try { const result = await api(`/sources/${source.sourceId}/probe`, { method: "POST" });
        state.statuses = state.statuses.filter((item) => item.sourceId !== source.sourceId).concat(result);
        if (result.connected === true) monitor.health.set(source.sourceId, new Date().toISOString());
        banner(result.connected === true ? "Связь с источником установлена." : result.connected === null ? "Для источника не задан URL проверки связи." : "Проверка связи не подтвердила доступность источника.", result.connected === true); renderSources();
      } catch (error) { banner(error.message); }
    }, "secondary"));
  container.append(actions);
  const grid = node("dl", undefined, "detail-grid");
  detailEntry(grid, "Идентификатор", source.sourceId); detailEntry(grid, "Тип / адаптер", `${source.type} / ${source.adapter}`);
  detailEntry(grid, "Адрес", source.baseUrl || source.filePath); detailEntry(grid, "Подписант", source.signer);
  detailEntry(grid, "Проверка подписи", source.verification);
  detailEntry(grid, "Версия адаптера", source.adapterVersion);
  detailEntry(grid, "Доступ к данным", source.type === "mock" ? "Демонстрационные данные" : source.access?.approved === true ? "Разрешён оператором" : "Не разрешён");
  if (source.access?.reference) detailEntry(grid, "Основание доступа", source.access.reference);
  if (source.access?.purpose) detailEntry(grid, "Цель доступа", source.access.purpose);
  container.append(grid);
  container.append(node("h3", "Что подтверждает источник"));
  for (const fact of source.factTypes) container.append(node("p", factTitle(fact), "source-fact-label"));
  const schemas = node("details"); schemas.append(node("summary", "Технические схемы ответов"), node("pre", pretty(source.factSchemas))); container.append(schemas);
  const processes = state.processes.filter((process) => Object.values(process.requirements).some((rule) => rule.sources.includes(source.sourceId)));
  const consumers = state.workflows.filter((workflow) => processes.some((process) => process.processId === workflow.processId));
  container.append(node("h3", "Бизнес-процессы"));
  for (const process of processes) container.append(button(process.name, () => editProcess(process)));
  container.append(node("h3", "Контракты-потребители"));
  container.append(node("p", consumers.length ? consumers.map((workflow) => workflow.name).join(", ") : "Источник пока не привязан к контрактам.", "description"));
  const audit = node("div"); container.append(node("h3", "Последние проверки"), audit);
  $("detail-dialog").showModal();
  try {
    const data = await api(`/sources/${source.sourceId}/audit`);
    for (const item of data.evidence) {
      const entry = node("div", undefined, "audit-entry");
      entry.append(badge(item.accepted ? "Проверено" : "Отклонено", item.accepted ? "success" : "failed"),
        node("p", `${factTitle(item.fact_type)} · ${item.accepted ? "Проверено" : "Отклонено"}`, "mono"), button("Ответ и проверки →", () => inspectResponse(item)), node("span", `${new Date(item.created_at).toLocaleString()} · ${item.deal_id}`, "muted-text mono")); audit.append(entry);
    }
    if (!data.evidence.length) audit.append(node("p", "Источник ещё не участвовал в проверках.", "description"));
    container.append(node("h3", "История настроек"));
    for (const change of data.changes) {
      const entry = node("details"); entry.append(node("summary", `${change.previous ? "Изменение" : "Регистрация"} · ${new Date(change.created_at).toLocaleString()}`), node("pre", pretty({ before: change.previous, after: change.current }))); container.append(entry);
    }
  } catch (error) { audit.append(node("p", error.message, "error")); }
}
async function requestDetail(request) {
  $("detail-title").textContent = requestName(request); const container = $("detail-content");container.replaceChildren(node("p", "Загрузка аудита…", "description"));$("detail-dialog").showModal();
  try {await loadAudit(request.id, true);container.replaceChildren(renderTimeline(summarize(request)));}
  catch (error) {container.replaceChildren(node("p", error.message, "error"));}
}
function editSource(source = null) {
  state.editing = source;
  const form = $("source-form"); form.reset();
  const initial = source || { type: "http", adapter: "signed-http", timeout: 3, enabled: false,
    factSchemas: { TEMPERATURE: { type: "number" } }, factTypes: ["TEMPERATURE"] };
  $("source-form-title").textContent = source ? "Настройки источника" : "Добавить источник";
  $("adapter-options").replaceChildren(...state.caps.adapters.map((name) => { const option = node("option", name); option.value = name; return option; }));
  $("verification-options").replaceChildren(...state.caps.verificationMethods.map((name) => { const option = node("option", name); option.value = name; return option; }));
  form.elements.verification.value = initial.verification === "EIP-191 test signature" ? "eip191" : (initial.verification || "eip191");
  for (const name of ["sourceId", "name", "type", "adapter", "baseUrl", "healthUrl", "signer", "timeout", "method", "responsePath", "filePath"]) {
    form.elements[name].value = initial[name] ?? (name === "method" ? "POST" : "");
  }
  form.elements.sourceId.readOnly = Boolean(source);
  form.elements.name.value = sourceTitle(initial);
  form.elements.factTypes.value = (initial.factTypes || []).join(", ");
  form.elements.enabled.checked = Boolean(initial.enabled);
  for (const name of ["factSchemas", "fieldMap", "headersEnv", "verificationConfig", "requestBindings", "adapterConfig"]) form.elements[name].value = pretty(initial[name] || {});
  form.elements.approved.checked = initial.access?.approved === true;
  form.elements.reference.value = initial.access?.reference || "";
  form.elements.purpose.value = initial.access?.purpose || "";
  form.querySelector(".form-error").textContent = "";
  $("source-dialog").showModal();
}
function workflowTemplate() {
  const process = state.processes[0], fact = Object.keys(process?.requirements || {})[0] || "OBSERVATION";
  return { workflowId: "CUSTOM_CONSUMER", processId: process?.processId || "", name: "Новый контракт", network: Object.keys(state.caps.networks)[0],
    verifyingContract: "0x0000000000000000000000000000000000000000", abi: [], enabled: false,
    signerFunction: null, checks: [],
    attestation: { name: "Oracle Consumer", version: "1", typeName: "Observation", ttl: 120,
      fields: [{ name: "requestId", type: "bytes32" }, { name: "confirmed", type: "bool" },
        ...["issuedAt", "expiresAt", "nonce"].map((name) => ({ name, type: "uint256" }))],
      bindings: { requestId: { context: "dealId" }, confirmed: { fact },
        issuedAt: { runtime: "issuedAt" }, expiresAt: { runtime: "expiresAt" }, nonce: { runtime: "nonce" } } } };
}
function editJson(kind, value = null) {
  state.jsonKind = kind; state.jsonId = value?.workflowId || null;
  $("json-title").textContent = kind === "workflow" ? (value ? value.name : "Подключить контракт") : "Добавить сделку";
  $("json-description").textContent = kind === "workflow"
    ? "Выберите бизнес-процесс. Здесь настраиваются только сеть, ABI, состояние контракта и EIP-712 bindings."
    : "Укажите реквизиты сделки и связанный контракт. Реквизиты сохраняются один раз.";
  const template = kind === "workflow" ? workflowTemplate() : { requestId: "0x" + "00".repeat(32), workflowId: state.workflows[0]?.workflowId || "", subjectRef: "request-001", parameters: {} };
  const initial = { ...(value || template) };
  $("integration-process-label").hidden = kind !== "workflow";
  $("integration-process").replaceChildren(...state.processes.map((process) => {const option = node("option", process.name); option.value = process.processId; return option;}));
  $("integration-process").value = initial.processId || "";
  if (kind === "workflow") delete initial.processId;
  $("json-input").value = pretty(initial);
  $("json-form").querySelector(".form-error").textContent = "";
  $("json-dialog").showModal();
}
async function updateDemo() {return loadDealPanel(true);}
async function refresh() {
  if (state.busy) return;
  state.busy = true;
  try {
    const [sources, processes, workflows, requests, statuses, caps] = await Promise.all([
      api("/sources"), api("/processes"), api("/workflows"), api("/requests"), api("/sources/status"), state.caps || api("/capabilities")]);
    if ($("banner").textContent.startsWith("Нет связи с сервером.")) banner("");
    for (const health of statuses) if (health.connected === true) monitor.health.set(health.sourceId, new Date().toISOString());
    for (const request of requests) if (pretty(state.requests.find((r) => r.id === request.id)?.result) !== pretty(request.result)) monitor.audit.delete(request.id);
    state.loadingCatalog = false;
    state.sources = sources.map(cleanSource); state.processes = processes; state.workflows = workflows; state.requests = requests; state.statuses = statuses; state.caps = caps;
    renderSources(); if (typeof renderProcesses === "function") renderProcesses(); renderWorkflows(); renderRequests();
    if (!$("page-deal").hidden) await updateDemo();
    if (!$("page-processes").hidden && typeof loadProcessResult === "function") await loadProcessResult();
    if (!$("page-requests").hidden) {await loadProcessResult(); await hydrateHistory();}
  } catch {
    state.loadingCatalog = false;
    state.statuses = []; renderSources();
    banner("Нет связи с сервером. Показанные настройки могут быть устаревшими.");
    state.demoResultError = "Нет связи с сервером. Результат сделки не обновлён.";
    // Keep the last rendered deal and its expanded sections during transient failures.
    if (typeof renderProcessResults === "function") renderProcessResults();
  } finally { state.busy = false; }
}
function showPage(name) {
  const mainPage = name === "deal" ? "processes" : name === "responses" ? "requests" : name;
  document.querySelectorAll("nav [data-page]").forEach((item) => item.classList.toggle("active", item.dataset.page === mainPage));
  document.querySelectorAll(".page").forEach((page) => {page.hidden = page.id !== `page-${name}`;});
  if (name === "deal") loadDealPanel(true);
  if (name === "processes") loadProcessResult();
  if (name === "responses") {syncResponseFilters(); loadResponses();}
  if (name === "requests") {renderRequests(); loadProcessResult().then(hydrateHistory);}
  window.scrollTo(0, 0);
}
document.querySelectorAll("[data-page]").forEach((element) => element.addEventListener("click", () => showPage(element.dataset.page)));
document.querySelectorAll(".close-dialog").forEach((element) => element.addEventListener("click", () => element.closest("dialog").close()));
for (const name of ["source-search", "source-type", "source-state"]) $(name).addEventListener("input", renderSources);
$("demo-refresh").addEventListener("click", updateDemo);
$("history-process").addEventListener("change", renderRequests);
$("history-status").addEventListener("change", renderRequests);
$("add-source").addEventListener("click", () => { if (state.caps) editSource(); });
$("add-workflow").addEventListener("click", () => editJson("workflow"));
$("add-request").addEventListener("click", () => editJson("request"));
$("source-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const form = event.target, values = new FormData(form);
  try {
    const source = { ...(state.editing || {}), sourceId: values.get("sourceId"), name: values.get("name"),
      type: values.get("type"), adapter: values.get("adapter"), adapterConfig: JSON.parse(values.get("adapterConfig")), baseUrl: values.get("baseUrl"), healthUrl: values.get("healthUrl"),
      signer: values.get("signer"), factTypes: values.get("factTypes").split(",").map((fact) => fact.trim()).filter(Boolean),
      enabled: form.elements.enabled.checked, timeout: Number(values.get("timeout")), method: values.get("method"),
      responsePath: values.get("responsePath"), filePath: values.get("filePath"), verification: values.get("verification"), verificationConfig: JSON.parse(values.get("verificationConfig")),
      requestBindings: JSON.parse(values.get("requestBindings")), factSchemas: JSON.parse(values.get("factSchemas")), fieldMap: JSON.parse(values.get("fieldMap")),
      headersEnv: JSON.parse(values.get("headersEnv")), access: { approved: form.elements.approved.checked,
        reference: values.get("reference"), purpose: values.get("purpose") } };
    await api(state.editing ? `/sources/${source.sourceId}` : "/sources", { method: state.editing ? "PUT" : "POST", body: pretty(source) });
    $("source-dialog").close(); banner(""); await refresh();
  } catch (error) { form.querySelector(".form-error").textContent = error.message; }
});
$("json-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const form = event.target;
  try {
    const path = state.jsonKind === "request" ? "/requests" : state.jsonId ? `/workflows/${state.jsonId}` : "/workflows";
    const definition = JSON.parse($("json-input").value);
    if (state.jsonKind === "workflow") definition.processId = $("integration-process").value;
    await api(path, { method: state.jsonId ? "PUT" : "POST", body: pretty(definition) });
    $("json-dialog").close(); banner(""); await refresh();
  } catch (error) { form.querySelector(".form-error").textContent = error.message; }
});
async function poll() { await refresh(); setTimeout(poll, 7000); }
poll();

