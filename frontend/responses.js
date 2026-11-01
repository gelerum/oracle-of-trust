const responseState = {items: [], cursor: null, epoch: 0};
const responseLabels = {VERIFIED: "Проверено", REJECTED: "Отклонено", NOT_CHECKED: "Не проверено", READY: "Выполнено", WAITING: "Ожидание"};
function responseBadge(status) {
  return badge(responseLabels[status] || "Не сохранено", statusStyle(status));
}
function responseTransport(item) {
  const transport = item.details?.transport || {};
  const labels = {received: "Получен", error: "Ошибка HTTP", timeout: "Таймаут", unavailable: "Недоступен", not_requested: "Не запрошен"};
  return [labels[transport.status] || "Не сохранено", transport.httpStatus !== undefined ? `HTTP ${transport.httpStatus}` : ""].filter(Boolean).join(" · ");
}
function responseValue(item) {
  const raw = item.raw || {};
  if (raw.retrievalStatus && raw.retrievalStatus !== "present") return ({unknown: "Пока неизвестно", unavailable: "Нет данных", rejected: "Отказ источника"})[raw.retrievalStatus] || raw.retrievalStatus;
  return Object.hasOwn(raw, "value") ? displayValue(raw.value) : "Нет значения";
}
function syncResponseFilters() {
  for (const [id, entries, key, label] of [["response-source", state.sources, "sourceId", "name"], ["response-request", state.requests, "id", null]]) {
    const select = $(id), selected = select.value;
    select.replaceChildren(node("option", id === "response-source" ? "Все источники" : "Все запросы"));
    select.firstChild.value = "";
    for (const entry of entries) { const option = node("option", id === "response-source" ? sourceTitle(entry) : label ? entry[label] : `${entry.data.subjectRef} · ${entry.id}`); option.value = entry[key]; select.append(option); }
    if (selected && !entries.some((entry) => entry[key] === selected)) {const option = node("option", selected); option.value = selected; select.append(option);}
    select.value = selected;
  }
}
function renderResponseRows() {
  const body = $("response-rows"); body.replaceChildren();
  for (const item of responseState.items) {
    const row = node("tr"), source = node("td"), request = node("td");
    source.append(node("strong", sourceTitle(state.sources.find((s) => s.sourceId === item.source_id) || {name: item.source_id})), node("span", new Date(item.created_at).toLocaleString("ru-RU"), "sub"));
    request.append(node("strong", factTitle(item.fact_type)), node("span", item.subject_ref || item.deal_id, "sub mono"));
    const validation = node("td"), rules = node("td"), action = node("td");
    validation.append(responseBadge(item.details?.validation?.status || (item.accepted ? "VERIFIED" : null)));
    rules.append(responseBadge(item.details?.rules?.status));
    action.append(button("Открыть →", () => inspectResponse(item)));
    row.append(source, request, node("td", responseTransport(item)), node("td", responseValue(item), "response-value mono"), validation, rules, action); body.append(row);
  }
  $("responses-empty").hidden = responseState.items.length !== 0;
  $("responses-more").hidden = !responseState.cursor;
}
async function loadResponses(more = false) {
  const epoch = ++responseState.epoch;
  const query = new URLSearchParams({limit: "50"});
  for (const [id, key] of [["response-source", "sourceId"], ["response-request", "requestId"], ["response-fact", "factType"]]) if ($(id).value.trim()) query.set(key, $(id).value.trim());
  if (more && responseState.cursor) query.set("before", responseState.cursor);
  if (!more) { responseState.items = []; responseState.cursor = null; renderResponseRows(); $("responses-empty").hidden = true; $("responses-count").textContent = "Загрузка ответов…"; }
  $("responses-refresh").disabled = true; $("responses-more").disabled = true;
  try {
    const data = await api(`/responses?${query}`);
    if (epoch !== responseState.epoch) return;
    responseState.items = more ? responseState.items.concat(data.items) : data.items;
    responseState.cursor = data.nextCursor;
    renderResponseRows(); $("responses-error").hidden = true;
    $("responses-count").textContent = `Показано ${responseState.items.length} из ${data.total}. HTTP-ответ, доказательство и бизнес-условие проверяются отдельно.`;
  } catch (error) {if (epoch === responseState.epoch) {$("responses-error").textContent = error.message; $("responses-error").hidden = false;}}
  finally {if (epoch === responseState.epoch) {$("responses-refresh").disabled = false; $("responses-more").disabled = false;}}
}
function openResponses(filters = {}) {
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  syncResponseFilters();
  for (const [id, key] of [["response-source", "sourceId"], ["response-request", "requestId"], ["response-fact", "factType"]]) {
    const element = $(id), value = filters[key] || "";
    if (element.tagName === "SELECT" && value && ![...element.options].some((option) => option.value === value)) {const option = node("option", value); option.value = value; element.append(option);}
    element.value = value;
  }
  showPage("responses");
}
function inspectResponse(item) {
  const container = $("response-content"), details = item.details || {}, raw = item.raw || {};
  container.replaceChildren();
  const grid = node("dl", undefined, "detail-grid");
  for (const [title, value] of [["Источник", item.source_id], ["Запрос", item.deal_id], ["Тип факта", factTitle(item.fact_type)], ["Получен backend", new Date(item.created_at).toLocaleString("ru-RU")], ["Получение", responseTransport(item)], ["Статус данных", retrievalTitle(raw.retrievalStatus)], ["Значение", responseValue(item)], ["Причина / результат", reasonText(item.reason)], ["Метод проверки", details.verificationMethod || "Не сохранено"], ["Доверенный подписант", details.sourceSigner || "Не сохранено"]]) detailEntry(grid, title, value);
  container.append(grid, responseFields(item), node("h3", "Проверка доказательства"), responseBadge(details.validation?.status || (item.accepted ? "VERIFIED" : null)));
  const labels = {schema: "Схема", source: "Полномочия источника", request: "Идентификатор запроса", lifetime: "Срок действия", origin: "Подпись / происхождение", document: "Документ и реквизиты", value: "Формат значения", replay: "Защита от повторов", access: "Доступ к источнику"};
  for (const [key, status] of Object.entries(details.validation?.checks || {})) {const row = node("div", undefined, "row"); row.append(node("span", labels[key] || key), responseBadge(status)); container.append(row);}
  container.append(node("h3", "Бизнес-условие"), responseBadge(details.rules?.status));
  for (const reason of details.rules?.reasons || []) container.append(node("p", reasonText(reason), "error"));
  if (raw.expiresAt) container.append(node("p", `Срок доказательства: ${new Date(raw.expiresAt * 1000).toLocaleString("ru-RU")}. ${raw.expiresAt * 1000 <= Date.now() ? "Сейчас истёк." : "Сейчас не истёк."} Результаты выше относятся к моменту проверки.`, "hint"));
  container.append(node("h3", "Исходный ответ источника"));
  if (Object.hasOwn(details, "originalResponse")) {
    try {
      const original = JSON.parse(details.originalResponse);
      const find = (value) => {if (!value || typeof value !== "object") return null;if (value.evidenceId) return value;for (const key of ["documentEvidence", "bankDecision", "data", "evidence"]) {const found = find(value[key]);if (found) return found;}return null;};
      const evidence = find(original);
      container.append(evidence ? node("pre", pretty(safeEvidence(evidence))) : node("p", "Произвольное содержимое ответа скрыто. Доступны статус получения и нормализованные реквизиты.", "description"));
    } catch {container.append(node("p", "Текстовый ответ сохранён в backend и не показывается в панели.", "description"));}
  } else container.append(node("p", "Исходный ответ не сохранён или не был получен.", "description"));
  container.append(node("h3", "Нормализованное доказательство"), node("pre", pretty(safeEvidence(raw))));
  $("response-dialog").showModal();
}
$("responses-refresh").addEventListener("click", () => loadResponses());
$("responses-apply").addEventListener("click", () => loadResponses());
$("responses-more").addEventListener("click", () => loadResponses(true));
for (const id of ["response-source", "response-request"]) $(id).addEventListener("change", () => loadResponses());
$("response-fact").addEventListener("keydown", (event) => {if (event.key === "Enter") loadResponses();});
