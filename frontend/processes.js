let processRenderKey = "";
function renderProcesses() {
  const key = pretty([state.processes, state.sources, state.workflows, state.statuses, state.requests, state.loadingCatalog]);
  if (key === processRenderKey) return;
  processRenderKey = key;
  const container = $("process-cards"); container.replaceChildren();
  for (const process of state.processes) {
    const facts = Object.entries(process.requirements), sourceIds = [...new Set(facts.flatMap(([, rule]) => rule.sources))];
    const integrations = state.workflows.filter((w) => w.processId === process.processId);
    const card = node("article", undefined, "business-process");
    const heading = node("div", undefined, "business-heading"), title = node("div");
    title.append(node("p", "НАСТРОЙКА ПРОЦЕССА", "eyebrow"), node("h2", process.name), node("p", `Источников: ${sourceIds.length} · Обязательных подтверждений: ${facts.length}`, "description"));
    const actions = node("div", undefined, "action-group");
    actions.append(badge(process.enabled ? "Включён" : "Выключен", process.enabled ? "success" : "muted"), button("Настроить процесс", () => editProcess(process), "secondary"));
    heading.append(title, actions); card.append(heading);
    const linked = node("div", undefined, "process-linked"); linked.append(node("span", "Смарт-контракт: "));
    for (const integration of integrations) linked.append(button(integration.name, () => showPage("workflows")));
    if (!integrations.length) linked.append(node("span", "не подключён")); card.append(linked);
    const sourceGrid = node("div", undefined, "process-sources");
    sourceGrid.setAttribute("aria-label", "Источники и подтверждения процесса");
    for (const id of sourceIds) {
      const source = state.sources.find((s) => s.sourceId === id), group = node("section", undefined, "process-source");
      const supplied = facts.filter(([, rule]) => rule.sources.includes(id));
      const label = node("div", undefined, "process-source-heading");
      label.append(source ? button(sourceTitle(source), () => sourceDetail(source), "process-source-name") : node("strong", id), node("span", `Подтверждений: ${supplied.length}`, "description"));
      group.append(label);
      const list = node("ul", undefined, "process-source-facts");
      for (const [fact] of supplied) list.append(node("li", factTitle(fact)));
      group.append(list); sourceGrid.append(group);
    }
    card.append(sourceGrid);
    const dealsHeading = node("div", undefined, "process-deals-heading");
    dealsHeading.append(node("h3", "Сделки"), button("+ Добавить сделку", () => {
      editJson("request");
      const context = JSON.parse($("json-input").value); context.workflowId = integrations[0]?.workflowId || ""; $("json-input").value = pretty(context);
    }, "secondary")); card.append(dealsHeading);
    const requestList = node("div", undefined, "process-request-list");
    for (const request of state.requests.filter((r) => integrations.some((w) => w.workflowId === r.data.dealType))) {
      const row = node("div", undefined, "row");
      row.append(button(request.data.subjectRef.startsWith("test-supply-742") ? "Сделка #742" : request.data.subjectRef, () => {
        if (state.demoSnapshot?.dealId === request.id) showDeal(request.id); else requestDetail(request);
      }), badge("Проверка: " + statusTitle(request.result?.status || "WAITING")));
      row.dataset.requestId = request.id; requestList.append(row);
    }
    card.append(requestList);
    const outcome = node("section", undefined, "process-outcome"); outcome.dataset.processId = process.processId; outcome.hidden = true; card.append(outcome);
    const rulesDetail = node("details", undefined, "process-rules-detail"); rulesDetail.append(node("summary", "Все условия и реквизиты"));
    const ruleHeading = node("div", undefined, "business-table-heading");
    ruleHeading.append(node("h3", "Условия и реквизиты")); rulesDetail.append(ruleHeading);
    const table = node("table", undefined, "business-table"), head = node("thead"), hr = node("tr");
    for (const title of ["Что должно произойти", "Кто подтверждает", "Что проверяется"]) hr.append(node("th", title)); head.append(hr); table.append(head);
    const body = node("tbody");
    for (const [fact, rule] of facts) {
      const row = node("tr"), what = node("td"), who = node("td"), checks = node("td");
      what.append(node("strong", factTitle(fact)));
      for (const id of rule.sources) {const source = state.sources.find((s) => s.sourceId === id); who.append(source ? button(sourceTitle(source), () => sourceDetail(source)) : node("span", id));}
      checks.append(node("strong", conditionText(rule)), readableTerms(rule)); row.append(what, who, checks); body.append(row);
    }
    table.append(body); const scroll = node("div", undefined, "business-table-scroll"); scroll.append(table); rulesDetail.append(scroll);
    const bottom = node("div", undefined, "business-bottom");
    bottom.append(node("p", "Подробности настроенных проверок.", "description"), button("Перейти к запросам →", () => document.querySelector('[data-page="requests"]').click()));
    const contracts = node("div", undefined, "process-contracts"); contracts.append(node("h3", "Связанные контракты"));
    for (const integration of integrations) contracts.append(button(integration.name, () => showPage("workflows")));
    bottom.append(contracts);
    const technical = node("details", undefined, "business-identifiers"); technical.append(node("summary", "Технические идентификаторы"));
    const ids = node("dl", undefined, "detail-grid"); detailEntry(ids, "Процесс", process.processId);
    for (const [fact] of facts) detailEntry(ids, factTitle(fact), fact); technical.append(ids); bottom.append(technical); rulesDetail.append(bottom); card.append(rulesDetail); container.append(card);
  }
  renderProcessResults();
  if (!state.processes.length) container.append(node("p", state.loadingCatalog ? "Загрузка процессов…" : "Нет загруженных процессов. Создайте процесс или проверьте соединение с сервером.", "empty"));
}
function processField(title, input) {const label = node("label", title); label.append(input); return label;}
function addProcessFact(fact = "", rule = {sources: [], equals: true}) {
  const card = node("div", undefined, "process-fact"), grid = node("div", undefined, "form-grid");
  const factHeading = node("h3", factTitle(fact) || "Новое подтверждение", "fact-editor-title"); card.append(factHeading);
  const factInput = node("input"); factInput.name = "fact"; factInput.value = fact; factInput.required = true; factInput.setAttribute("list", "known-facts");
  const sources = node("select"); sources.name = "sources"; sources.multiple = true; sources.required = true;
  function sourceOptions() {
    const selected = [...sources.selectedOptions].map((o) => o.value);
    sources.replaceChildren();
    for (const source of state.sources.filter((s) => s.factTypes.includes(factInput.value))) {
      const option = node("option", `${sourceTitle(source)}${source.enabled ? "" : " · выключен"}`); option.value = source.sourceId;
      option.selected = (selected.length ? selected : rule.sources).includes(source.sourceId); sources.append(option);
    }
    for (const id of rule.sources.filter((id) => ![...sources.options].some((o) => o.value === id))) {const option = node("option", `${id} · не поддерживает факт`); option.value = id; option.selected = true; sources.append(option);}
  }
  sourceOptions(); factInput.addEventListener("change", () => {sourceOptions(); factHeading.textContent = factTitle(factInput.value) || "Новое подтверждение";});
  const op = node("select"); op.name = "operator";
  for (const [value, text] of [["eq", "Равно"], ["ne", "Не равно"], ["gt", "Больше"], ["gte", "Не меньше"], ["lt", "Меньше"], ["lte", "Не больше"], ["in", "Входит в список"]]) {const option = node("option", text); option.value = value; op.append(option);}
  const condition = rule.condition || {op: "eq", value: rule.equals}; op.value = condition.op;
  const origin = node("select"); origin.name = "expectedOrigin";
  for (const [value, text] of [["value", "Заданное значение"], ["context", "Реквизит запроса"]]) {const option = node("option", text); option.value = value; origin.append(option);}
  origin.value = Object.hasOwn(condition, "context") ? "context" : "value";
  const expected = node("input"); expected.name = "expected"; expected.required = true; expected.value = origin.value === "context" ? condition.context : pretty(condition.value);
  const expectedLabel = processField("Требуемое значение", expected);
  const booleanChoice = node("select"); booleanChoice.name = "booleanAnswer";
  for (const [value, text] of [["true", "Да · факт подтверждён"], ["false", "Нет · отрицательный факт"], ["custom", "Другое значение / реквизит"]]) {const option = node("option", text); option.value = value; booleanChoice.append(option);}
  const useBoolean = origin.value === "value" && typeof condition.value === "boolean";
  booleanChoice.value = useBoolean ? String(condition.value) : "custom";
  expected.hidden = useBoolean; expectedLabel.append(booleanChoice);
  booleanChoice.addEventListener("change", () => {expected.hidden = booleanChoice.value !== "custom"; if (expected.hidden) {origin.value = "value"; expected.value = booleanChoice.value;}});
  origin.addEventListener("change", () => {if (origin.value === "context") {booleanChoice.value = "custom"; expected.hidden = false;}});
  grid.append(processField("Тип факта", factInput), processField("Доверенные источники · один или несколько", sources), processField("Условие", op), processField("Сравнить с", origin), expectedLabel);
  const preview = node("div", undefined, "terms-preview");
  preview.append(node("strong", "Дополнительно сверяются"), readableTerms(rule));
  const extra = node("details"); extra.append(node("summary", "Расширенная настройка реквизитов · JSON"));
  for (const [name, title] of [["attributes", "Поле ответа → реквизит запроса · JSON"], ["required", "Обязательные значения в ответе · JSON"]]) {const input = node("textarea"); input.name = name; input.className = "code"; input.rows = 3; input.value = pretty(rule[name] || {}); extra.append(processField(title, input));}
  card.append(grid); if (termDescriptions(rule).length) card.append(preview);
  card.append(extra, button("Убрать факт", () => card.remove()));
  $("process-facts").append(card);
}
function editProcess(process = null) {
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  state.editingProcess = process;
  const form = $("process-form"); form.reset();
  $("process-title").textContent = process?.name || "Добавить бизнес-процесс";
  form.elements.processId.value = process?.processId || ""; form.elements.processId.readOnly = Boolean(process);
  form.elements.name.value = process?.name || ""; form.elements.enabled.checked = process?.enabled ?? true;
  form.elements.contextSchema.value = pretty(process?.contextSchema || {type: "object"});
  $("process-facts").replaceChildren(); $("known-facts").replaceChildren(...[...new Set(state.sources.flatMap((s) => s.factTypes))].map((fact) => {const option = node("option"); option.value = fact; return option;}));
  for (const [fact, rule] of Object.entries(process?.requirements || {})) addProcessFact(fact, rule);
  if (!process) addProcessFact();
  form.querySelector(".form-error").textContent = ""; $("process-dialog").showModal();
}
$("add-process").addEventListener("click", () => editProcess());
$("add-process-fact").addEventListener("click", () => addProcessFact());
$("process-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const form = event.target;
  try {
    const requirements = {};
    for (const card of $("process-facts").children) {
      const input = (name) => card.querySelector(`[name="${name}"]`);
      const fact = input("fact").value.trim();
      if (!fact || Object.hasOwn(requirements, fact)) throw new Error("Укажите разные непустые типы фактов.");
      const origin = input("expectedOrigin").value;
      const rule = {sources: [...input("sources").selectedOptions].map((o) => o.value), condition: {op: input("operator").value,
        [origin]: origin === "context" ? input("expected").value.trim() : JSON.parse(input("expected").value)}};
      for (const key of ["attributes", "required"]) {const value = JSON.parse(input(key).value); if (Object.keys(value).length) rule[key] = value;}
      requirements[fact] = rule;
    }
    const definition = {processId: form.elements.processId.value.trim(), name: form.elements.name.value.trim(), enabled: form.elements.enabled.checked,
      contextSchema: JSON.parse(form.elements.contextSchema.value), requirements};
    await api(state.editingProcess ? `/processes/${definition.processId}` : "/processes", {method: state.editingProcess ? "PUT" : "POST", body: pretty(definition)});
    $("process-dialog").close(); banner("Бизнес-процесс сохранён.", true); await refresh();
  } catch (error) {form.querySelector(".form-error").textContent = error.message;}
});

renderProcesses();

if (state.caps && !$("page-processes").hidden) loadProcessResult();
