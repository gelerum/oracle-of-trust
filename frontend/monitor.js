const monitor = {audit: new Map(), health: new Map(), selected: null, epoch: 0};
const formatTime = (value) => value ? new Date(typeof value === "number" ? value * 1000 : value).toLocaleString("ru-RU") : "Время не предоставлено API";
const money = (value, decimals = 6, unit = "₽") => typeof value === "number" ? new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 2}).format(value / 10 ** decimals) + " " + unit : "Нет данных";
function statusStyle(status) {return ["VERIFIED", "READY", "SIGNED", "CONFIRMED"].includes(status) ? "success" : ["REJECTED", "EXPIRED", "ERROR"].includes(status) ? "failed" : ["WAITING", "PROCESSING"].includes(status) ? "waiting" : "muted";}
function requestName(request) {return request.id === state.demoSnapshot?.dealId ? `Сделка #${state.demoSnapshot.displayId || 742}` : request.data.subjectRef;}
function payloadField(workflow, payload, semantic) {
  const name = Object.keys(workflow?.attestation?.bindings || {}).find((key) => workflow.attestation.bindings[key].runtime === semantic || workflow.attestation.bindings[key].context === semantic);
  return payload?.[name || semantic];
}
function networkName(workflow) {return workflow?.network === "local" && state.caps?.networks?.local?.chainId === 31337 ? "Anvil / Local" : workflow?.network || "Нет данных";}
function requestProfile(request) {return state.workflows.find((w) => w.workflowId === request.data.dealType);}
function requestProcess(request) {return state.processes.find((p) => p.processId === requestProfile(request)?.processId);}
function snapshotFor(request) {return state.demoSnapshot?.dealId === request.id && !state.demoResultError ? state.demoSnapshot : null;}
async function loadAudit(requestId, force = false) {
  const cached = monitor.audit.get(requestId);
  if (!force && cached?.items) return cached.items;
  if (cached?.pending) return cached.pending;
  const entry = {...cached, error: null}; monitor.audit.set(requestId, entry);
  entry.pending = api(`/requests/${encodeURIComponent(requestId)}/evidence`).then((items) => {entry.items = items;return items;}).catch((error) => {entry.error = error.message; throw error;}).finally(() => {entry.pending = null;});
  return entry.pending;
}
function summarize(request) {
  const snapshot = snapshotFor(request), record = monitor.audit.get(request.id), workflow = requestProfile(request), process = requestProcess(request);
  const context = {...request.data, ...request.data.parameters};
  const requirements = process?.requirements || {}, items = record?.items || [], latest = {};
  for (const row of items) if (requirements[row.fact_type]) latest[row.fact_type] = row;
  const checks = snapshot?.checks || Object.fromEntries(Object.keys(requirements).map((fact) => {
    const row = latest[fact], rules = row?.details?.rules?.status;
    const expired = row?.raw?.expiresAt && row.raw.expiresAt * 1000 <= Date.now();
    return [fact, rules === "READY" && row.accepted && !expired ? "VERIFIED" : rules === "REJECTED" || row?.details?.validation?.status === "REJECTED" ? "REJECTED" : "WAITING"];
  }));
  const execution = snapshot?.execution || {}, paid = execution.status === "CONFIRMED";
  const evaluation = paid ? "READY" : snapshot?.evaluationStatus || request.result?.status || "WAITING";
  const signedResult = request.result?.attestation || (request.result?.signature ? request.result.payload : null);
  const payload = paid ? execution.attestation || signedResult : signedResult;
  const expiresAt = payloadField(workflow, payload, "expiresAt"), issuedAt = payloadField(workflow, payload, "issuedAt");
  const signature = paid ? "SIGNED" : payload ? expiresAt && expiresAt * 1000 <= Date.now() ? "EXPIRED" : "SIGNED" : "NOT_CHECKED";
  const reasons = paid ? [] : [...new Set([...(request.result?.reasons || []), ...items.filter((r) => latest[r.fact_type] === r && (!r.accepted || r.details?.rules?.status === "REJECTED")).flatMap((r) => r.details?.rules?.reasons?.length ? r.details.rules.reasons : [r.reason])])];
  const states = Object.values(checks), count = states.filter((v) => v === "VERIFIED").length;
  const received = Object.keys(requirements).filter((fact) => latest[fact]?.raw?.evidenceId).length;
  const stages = [received === states.length && received > 0 ? "VERIFIED" : record?.error ? "NOT_CHECKED" : received > 0 || items.length ? "WAITING" : "NOT_CHECKED", paid || evaluation === "READY" ? "VERIFIED" : evaluation === "REJECTED" ? "REJECTED" : items.length ? "WAITING" : "NOT_CHECKED", signature === "SIGNED" ? "VERIFIED" : signature === "EXPIRED" ? "EXPIRED" : "NOT_CHECKED", paid ? "CONFIRMED" : execution.status === "ERROR" || execution.status === "FAILED" ? "ERROR" : execution.status === "NOT_EXECUTED" && signature === "SIGNED" ? "WAITING" : "NOT_CHECKED"];
  const chainError = snapshot?.error || (state.demoSnapshot?.dealId === request.id ? state.demoResultError : null);
  return {request, context, snapshot, workflow, process, items, latest, checks, execution, paid, evaluation, payload, signature, issuedAt, expiresAt, reasons, count, chainError, total: states.length, stages, loading: !record?.items && Boolean(record?.pending), error: record?.error, date: items.at(-1)?.created_at || null};
}
function reasonText(reason) {
  let text = String(reason || "Причина не предоставлена");
  if (/private.?key|secret|password|authorization|api.?key|access.?token/i.test(text)) return "Ответ содержит закрытые поля. Подробности скрыты.";
  if (/validation error|extra_forbidden/i.test(text)) return "Ответ не соответствует зарегистрированной схеме данных";
  for (const [code, title] of Object.entries(factTitles)) text = text.replaceAll(code, title);
  const translations = {"negative or condition not satisfied": "источник не подтвердил факт или условие не выполнено", "negative or rejected": "источник отклонил подтверждение", "business attributes mismatch": "реквизиты ответа не совпадают с реквизитами сделки", "unauthorized source": "источник не уполномочен подтверждать факт", "unauthorized authority": "источник не разрешён правилом процесса", "unauthorized fact type": "источник не уполномочен подтверждать этот тип факта", "duplicate evidence": "повторное подтверждение", "expired or future evidence": "подтверждение просрочено или датировано будущим", "document mismatch": "хеш документа не совпадает", "deal mismatch": "идентификатор сделки не совпадает", "unavailable": "данные недоступны", "no enabled source": "нет включённого источника", "source access denied": "доступ к источнику не разрешён", "invalid source signature": "подпись источника недействительна", "source signature mismatch": "подпись источника не соответствует доверенному адресу", "source unavailable": "источник недоступен", "VERIFIED": "проверено"};
  for (const [from, to] of Object.entries(translations).sort((a,b) => b[0].length - a[0].length)) text = text.replaceAll(from, to);
  return text.replaceAll("ReadTimeout", "таймаут чтения").replaceAll("ConnectTimeout", "таймаут соединения").replaceAll("ConnectError", "ошибка соединения").replaceAll("HTTPStatusError", "ошибка HTTP");
}
function renderStages(model) {
  const list = node("ol", undefined, "control-stages");
  ["Получены данные", "Условия проверены", "Подпись оракула", "Выплата выполнена"].forEach((label, index) => {
    const stage = model.stages[index], item = node("li", undefined, statusStyle(stage));
    item.append(node("strong", `${index + 1}. ${label}`), node("span", ["VERIFIED", "CONFIRMED"].includes(stage) ? "Успешно" : ["ERROR", "REJECTED", "EXPIRED"].includes(stage) ? "Ошибка" : stage === "WAITING" ? "Ожидание" : "Не проверено"));list.append(item);
  });return list;
}
function renderReasons(model) {
  const box = node("div", undefined, "control-reasons");
  if (!model.reasons.length) return box;
  box.append(node("strong", "Почему выплата заблокирована"));
  const list = node("ul");for (const reason of model.reasons) list.append(node("li", reasonText(reason)));box.append(list);return box;
}
function transactionDetails(model) {
  const details = node("details", undefined, "control-technical");details.append(node("summary", "Транзакция финансирования"));
  const grid = node("dl", undefined, "detail-grid"), execution = model.execution;
  for (const [label, value] of [["Статус", model.paid ? "Исполнена" : execution.status === "NOT_EXECUTED" ? "Не исполнена" : "Подтверждение недоступно"], ["Хеш", execution.transactionHash || "Нет подтверждённого хеша"], ["Блок", execution.blockNumber ?? "Нет данных"], ["Сумма", model.paid ? money(execution.amount, model.snapshot?.settlement?.decimals ?? 6, "₽") : "Перевод не подтверждён"], ["Получатель", execution.supplier || "Не подтверждён"]]) detailEntry(grid, label, value);
  details.append(grid);return details;
}
function attestationDetails(model) {
  const details = node("details", undefined, "control-technical");details.append(node("summary", "Технические детали"));const payload = model.payload || {}, grid = node("dl", undefined, "detail-grid");
  for (const [label, value] of [["Адрес оракула", model.execution.signer || state.caps?.oracleSigner || "Нет данных"], ["Выпущено", model.issuedAt ? formatTime(model.issuedAt) : "Нет данных"], ["Действует до", model.expiresAt ? formatTime(model.expiresAt) : "Нет данных"], ["Хеш документа (documentHash)", payloadField(model.workflow, payload, "documentHash") || model.context.documentHash || "Нет данных"], ["Номер подтверждения (nonce)", payloadField(model.workflow, payload, "nonce") ?? "Нет данных"], ["Домен подписи", model.workflow?.attestation?.name || "Нет данных"], ["Версия домена", model.workflow?.attestation?.version || "Нет данных"]]) detailEntry(grid, label, value);
  details.append(node("p", model.paid ? "Это подтверждение уже принято смарт-контрактом при выплате. Окончание срока его действия не отменяет выполненную выплату." : "Срок действия определяет, до какого момента смарт-контракт может принять подтверждение для выплаты.", "description"), node("p", "Хеш документа связывает подтверждение с документом сделки. Номер подтверждения (nonce) защищает от повторного использования. Домен и его версия определяют формат проверки подписи.", "description"), grid);return details;
}
function historyEvents(model) {
  const events = [{title: "Сделка зарегистрирована", time: null, source: "Реестр сделок", result: "Реквизиты сохранены", style: "muted"}];
  for (const item of model.items) {
    if (["gateway", "registry"].includes(item.source_id) && !model.process?.requirements[item.fact_type]) {
      events.push({title: item.fact_type === "CHAIN_STATE" ? "Проверка состояния контракта" : "Системная проверка", time: item.created_at, source: "Оракул", result: reasonText(item.reason), style: item.accepted ? "success" : "failed"});continue;
    }
    events.push({title: `Ответ: ${factTitle(item.fact_type)}`, time: item.created_at, source: sourceTitle(state.sources.find((s) => s.sourceId === item.source_id) || {name: item.source_id}), result: item.accepted ? "Доказательство проверено" : reasonText(item.reason), style: item.accepted ? "success" : item.details?.rules?.status === "WAITING" ? "waiting" : "failed", evidence: item});
    if (["READY", "REJECTED"].includes(item.details?.rules?.status)) events.push({title: `Условие: ${factTitle(item.fact_type)}`, time: item.created_at, source: "Оракул", result: item.details.rules.status === "READY" ? "Условие выполнено" : item.details.rules.reasons.map(reasonText).join("; "), style: item.details.rules.status === "READY" ? "success" : "failed"});
  }
  if (model.payload) events.push({title: "Сформировано EIP-712-подтверждение", time: model.issuedAt || null, source: "Оракул", result: model.signature === "EXPIRED" ? "Срок действия истёк" : "Подписано", style: model.signature === "EXPIRED" ? "waiting" : "success"});
  if (model.paid) {
    events.push({title: "Транзакция передана в сеть", time: null, source: "Anvil", result: `Наличие транзакции подтверждено: ${model.execution.transactionHash}`, style: "success"});
    events.push({title: "Получено подтверждение финансирования", time: null, source: "Смарт-контракт", result: `Блок ${model.execution.blockNumber}; ${money(model.execution.amount, model.snapshot?.settlement?.decimals ?? 6, "₽")}; получатель ${model.execution.supplier || "не предоставлен API"}`, style: "success"});
  }
  const eventTime = (event) => !event.time ? event.title === "Сделка зарегистрирована" ? -Infinity : Infinity : typeof event.time === "number" ? event.time : Math.floor(new Date(event.time).getTime() / 1000);
  return events.sort((a,b) => eventTime(a) - eventTime(b));
}
function renderTimeline(model) {
  const list = node("ol", undefined, "control-timeline");
  for (const event of historyEvents(model)) {
    const item = node("li", undefined, event.style);item.append(node("strong", event.title), node("p", `${formatTime(event.time)} · ${event.source}`, "description"), node("p", event.result));
    if (event.evidence) item.append(button("Ответ и проверки →", () => inspectResponse(event.evidence)));list.append(item);
  }return list;
}
async function showDeal(requestId) {
  monitor.selected = requestId;
  showPage("deal");
}
async function loadDealPanel(force = true) {
  const request = state.requests.find((r) => r.id === monitor.selected) || state.requests.find((r) => r.id === state.demoSnapshot?.dealId);
  if (!request) {delete $("deal-control").dataset.requestId;$("deal-control").replaceChildren(node("p", "Сделка не найдена в реестре.", "empty"));return;}
  monitor.selected = request.id;const epoch = ++monitor.epoch;
  const current = $("deal-control");
  const sameDeal = current.dataset.requestId === request.id;
  if (!sameDeal) current.replaceChildren(node("p", "Загрузка проверок…", "description"));
  if (request.id === state.demoSnapshot?.dealId || !state.demoSnapshot) await loadProcessResult();
  try {await loadAudit(request.id, force);} catch {}
  if (epoch !== monitor.epoch || $("page-deal").hidden) return;
  const model = summarize(state.requests.find((r) => r.id === request.id) || request);
  const container = document.createDocumentFragment();
  $("deal-title").textContent = requestName(request);
  const overview = node("div", undefined, "control-overview");
  overview.append(resultMetric("Финансирование", money(model.paid ? model.execution.amount : model.snapshot?.settlement?.financingAmount ?? model.context.financingAmount, model.snapshot?.settlement?.decimals ?? 6, "₽")), resultMetric("Подтверждения", `${model.count} из ${model.total}`), resultMetric("Статус", model.paid ? "Финансирование исполнено" : model.chainError ? "Состояние неизвестно" : model.evaluation === "REJECTED" ? "Заблокировано" : "Ожидание исполнения"));container.append(overview);
  const parties = node("dl", undefined, "detail-grid deal-parties");
  for (const key of ["supplier", "buyer", "factor"]) detailEntry(parties, fieldTitle(key), model.context[key] || "Не предоставлен API");
  detailEntry(parties, "Срок оплаты", model.context.dueDate ? new Date(model.context.dueDate * 1000).toLocaleDateString("ru-RU") : "Нет данных");container.append(parties);
  if (model.chainError) container.append(node("p", "Состояние контракта недоступно: " + model.chainError, "error"));
  container.append(renderStages(model));if (model.error) container.append(node("p", `Не удалось получить аудит: ${model.error}`, "error"));
  container.append(renderReasons(model), node("h2", "Проверки"));
  const wrap = node("div", undefined, "table-scroll"), table = node("table"), head = node("tr");for (const text of ["Условие", "Источник", "Результат"]) head.append(node("th", text));const thead = node("thead");thead.append(head);table.append(thead);
  const tbody = node("tbody");for (const [fact, rule] of Object.entries(model.process?.requirements || {})) {
    const row = node("tr"), factCell = node("td");factCell.append(button(factTitle(fact), () => showFactAudit(model, fact)));
    const result = node("td");result.append(badge(statusTitle(model.checks[fact] || "NOT_CHECKED"), statusStyle(model.checks[fact] || "NOT_CHECKED")));
    const source = model.latest[fact]?.source_id;
    row.append(factCell, node("td", (source ? [source] : rule.sources).map((id) => sourceTitle(state.sources.find((s) => s.sourceId === id) || {name: id})).join(" / ")), result);tbody.append(row);
  }table.append(tbody);wrap.append(table);container.append(wrap);
  const sections = node("div", undefined, "control-sections"), oracle = node("section", undefined, "card control-box"), contract = node("section", undefined, "card control-box");
  oracle.append(node("h2", "Оракул"), node("p", "ВТБ Оракул Доверия проверяет подтверждения от источников по условиям сделки и подписывает результат. Смарт-контракт проверяет эту подпись перед выплатой.", "description"), node("p", model.evaluation === "READY" ? "Обязательные условия выполнены" : model.evaluation === "REJECTED" ? "Условия не выполнены" : "Ожидание подтверждений"), badge(model.signature === "SIGNED" ? model.paid ? "Подтверждение использовано при выплате" : "Подтверждение подписано" : model.signature === "EXPIRED" ? "Срок подписи истёк" : "Нет подписанного подтверждения", statusStyle(model.signature)), attestationDetails(model));
  contract.append(node("h2", "Смарт-контракт"), node("p", model.paid ? "Финансирование исполнено" : "Финансирование не подтверждено"), node("p", `Сеть: ${networkName(model.workflow)}`, "description"), node("p", model.workflow?.verifyingContract || "Адрес не предоставлен", "mono"), transactionDetails(model));
  contract.append(node("p", model.snapshot?.status === "SETTLED" ? "Задолженность погашена — по состоянию контракта" : "Задолженность покупателя не подтверждена как погашенная", "description"));
  sections.append(oracle, contract);container.append(sections);
  const timeline = node("details", undefined, "control-technical");timeline.append(node("summary", "Хронология аудита"), renderTimeline(model));container.append(timeline);
  // Capture UI state after the requests finish, including changes made while loading.
  const expanded = sameDeal ? [...current.querySelectorAll("details")].map((detail) => detail.open) : [];
  [...container.querySelectorAll("details")].forEach((detail, index) => {detail.open = expanded[index] || false;});
  const scrollX = window.scrollX, scrollY = window.scrollY;
  current.replaceChildren(container);
  current.dataset.requestId = request.id;
  if (sameDeal) window.scrollTo({left: scrollX, top: scrollY, behavior: "instant"});
}
function showFactAudit(model, fact) {
  $("detail-title").textContent = factTitle(fact);const container = $("detail-content");container.replaceChildren(node("h3", "Настроенные проверки реквизитов"), readableTerms(model.process.requirements[fact]));
  const rows = model.items.filter((item) => item.fact_type === fact);
  if (!rows.length) container.append(node("p", "Ответов источников по этому условию пока нет.", "description"));
  for (const item of rows.slice().reverse()) {
    const box = node("div", undefined, "audit-entry");box.append(node("strong", sourceTitle(state.sources.find((s) => s.sourceId === item.source_id) || {name: item.source_id})), node("p", formatTime(item.created_at), "description"), node("p", item.details?.rules?.status === "READY" ? "Сохранённый результат: условие выполнено" : reasonText(item.reason)), button("Реквизиты и ответ →", () => inspectResponse(item)));container.append(box);
  }
  $("detail-dialog").showModal();
}
async function hydrateHistory() {
  const pending = state.requests.map((r) => loadAudit(r.id));renderRequests();
  await Promise.allSettled(pending);renderRequests();
}
function safeAuditValue(value, depth = 0) {
  if (depth > 8) return "Скрыто";
  if (typeof value === "string") return value.length > 300 ? "Длинное содержимое скрыто" : value;
  if (Array.isArray(value)) return value.slice(0, 30).map((item) => safeAuditValue(item, depth + 1));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, /private.?key|secret|password|authorization|api.?key|access.?token|refresh.?token|document(?!hash)|credit.?agreement|loan.?agreement|file.?content|attachment|^(content|body|text|personalData)$/i.test(key) ? "Скрыто" : safeAuditValue(item, depth + 1)]));
  return value;
}
function safeEvidence(raw) {
  return safeAuditValue(Object.fromEntries(Object.entries(raw || {}).filter(([key]) => ["schemaVersion", "evidenceId", "sourceId", "factType", "dealId", "subjectRef", "value", "retrievalStatus", "documentHash", "observedAt", "expiresAt", "attributes", "sourceProof"].includes(key))));
}
function responseFields(item) {
  const request = state.requests.find((r) => r.id === item.deal_id), process = request && requestProcess(request), rule = process?.requirements[item.fact_type];
  const box = node("div");
  if (!rule || !request) return box;
  box.append(node("h3", "Реквизиты ответа и условия процесса"));
  const fields = Object.fromEntries(Object.entries(rule.attributes || {}).map(([key, path]) => {
    let expected = {...request.data, ...request.data.parameters}; for (const part of path.split(".")) expected = expected?.[part];return [key, expected];
  }));Object.assign(fields, rule.required || {});
  const wrap = node("div", undefined, "table-scroll"), table = node("table"), head = node("tr");for (const label of ["Реквизит", "Ответ источника", "Ожидается процессом"]) head.append(node("th", label));const thead = node("thead");thead.append(head);table.append(thead);const body = node("tbody");
  for (const [key, expected] of Object.entries(fields)) {const row = node("tr");row.append(node("td", fieldTitle(key)), node("td", displayValue(safeAuditValue(item.raw?.attributes?.[key] ?? "Не предоставлено"))), node("td", displayValue(safeAuditValue(expected ?? "Не предоставлено"))));body.append(row);}table.append(body);wrap.append(table);box.append(wrap, node("p", "Результат ниже сохранён в момент обработки ответа. Условия выше показаны из текущей конфигурации процесса.", "hint"));return box;
}
