import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = process.cwd();
const sourceRoots = [
  path.join(root, 'src', 'renderer'),
  path.join(root, 'src', 'shared'),
];

function allSource(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) return allSource(full);
    if (!/\.(tsx?|css)$/.test(entry.name)) return [];
    return [fs.readFileSync(full, 'utf8')];
  }).join('\n');
}

const source = sourceRoots.map(allSource).join('\n');
const appSource = fs.readFileSync(
  path.join(root, 'src', 'renderer', 'App.tsx'),
  'utf8',
);
const apiSource = fs.readFileSync(
  path.join(root, 'src', 'renderer', 'lib', 'api.ts'),
  'utf8',
);
const taskMediaPanelSource = fs.readFileSync(
  path.join(root, 'src', 'renderer', 'components', 'tasks', 'TaskMediaPanel.tsx'),
  'utf8',
);
const eventLineReportSource = fs.readFileSync(
  path.join(root, 'src', 'renderer', 'components', 'tasks', 'EventLineReportPanel.tsx'),
  'utf8',
);
const reportGeneratorSource = fs.readFileSync(
  path.join(root, 'src', 'renderer', 'components', 'reports', 'AIReportGeneratorModal.tsx'),
  'utf8',
);
const uiCompatSource = fs.readFileSync(
  path.join(root, 'backend', 'app', 'ui_compat.py'),
  'utf8',
);
const styles = fs.readFileSync(
  path.join(root, 'src', 'renderer', 'styles.css'),
  'utf8',
);
const intelligenceSource = fs.readFileSync(
  path.join(root, 'src', 'renderer', 'components', 'intelligence', 'IntelligenceStationView.tsx'),
  'utf8',
);
const topicsSource = fs.readFileSync(
  path.join(root, 'src', 'renderer', 'components', 'topics', 'TopicsManagementView.tsx'),
  'utf8',
);
const teamSyncSource = fs.readFileSync(
  path.join(root, 'src', 'renderer', 'components', 'settings', 'TeamSyncPanel.tsx'),
  'utf8',
);
const systemStatusSource = fs.readFileSync(
  path.join(root, 'src', 'renderer', 'components', 'global', 'SystemStatusPanel.tsx'),
  'utf8',
);
const mainSource = fs.readFileSync(
  path.join(root, 'src', 'main', 'main.ts'),
  'utf8',
);
const packageJson = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));

test('all frozen user-facing entry points remain present', () => {
  const required = [
    '任务与日程', '工作台', '战略陪伴', '资讯情报站', '成长中心',
    '协作收件箱', '任务列表', '我的月历', '组织计划', '事件线',
    '周复盘', '新建任务', '复杂任务 → AI 拆解+审批',
    '近期项目', '结构化问答引擎', '智能编辑', '文件', '收藏', '快捷工具',
    '目标与背景', '里程碑', '证据补充', '主线还原', '报告骨架', '项目报告',
    '客户档案', '判断 & 思考',
    '品牌监测', '时效情报',
    '经验墙', '能力成长', '徽章与排行',
    '账户', '组织与权限', 'AI 与云端', 'FEISHU · 飞书集成',
    '音频转文字', '后台深度解析', '音频与附件托管',
    '成长手册', '系统日志', '关于本软件', '反馈与建议',
    '登录组织', '加入组织', '创建组织', '工作空间',
  ];
  const missing = required.filter((label) => !source.includes(label));
  assert.deepEqual(missing, [], `missing frontend entries: ${missing.join('、')}`);
  assert.ok(appSource.includes('<FeishuOrgIntegrationPanel'));
  assert.ok(apiSource.includes("'/api/v2/ui/org-integrations/feishu'"));
  assert.ok(apiSource.includes("'/api/v2/ui/me/feishu-authorization'"));
});

test('event-line task search supports select-all and batch reference through the same authority command', () => {
  assert.ok(eventLineReportSource.includes('全选可引用任务'));
  assert.ok(eventLineReportSource.includes('批量引用'));
  assert.ok(eventLineReportSource.includes('selectedTaskCandidateIds'));
  assert.match(
    eventLineReportSource,
    /for \(const candidate of selected\)[\s\S]{0,1200}await linkTaskToEventLine\(/,
  );
});

test('event-line report inherits its project without redundant setup copy', () => {
  assert.ok(eventLineReportSource.includes('clientId={snapshot.eventLine.primaryClientId || undefined}'));
  assert.ok(reportGeneratorSource.includes('<Field label="时间范围">'));
  assert.equal(reportGeneratorSource.includes('本次使用：'), false);
  assert.equal(reportGeneratorSource.includes('报告模板：'), false);
});

test('renderer only calls v2 and never calls legacy endpoints', () => {
  assert.equal(source.includes('/api/v1'), false);
  assert.equal(source.includes('latest.yml'), false);
  assert.equal(source.includes('organization_cloud_proxy'), false);
  assert.ok(source.includes('/api/v2'));
});

test('task collaboration return goes back to the creator and time controls stay 24-hour', () => {
  assert.match(appSource, /returnedCreatorTasks/);
  assert.match(appSource, /负责人已退回/);
  assert.match(appSource, />修改</);
  assert.match(appSource, /TaskTime24Input/);
  assert.doesNotMatch(
    appSource,
    /label="开始"\s+value=\{editingTask\.dueTime\}\s+previewValue=/,
    '新建任务的开始时间不得带隐式预览默认值',
  );
  assert.match(appSource, /inputMode="numeric"/);
  assert.equal(/type=["']time["']/.test(source), false);
  assert.match(mainSource, /appendSwitch\('lang', 'zh-CN'\)/);
  assert.deepEqual(packageJson.build.mac.electronLanguages, ['zh_CN']);
});

test('all renderer mutations carry one stable idempotency key across retries', () => {
  assert.match(
    apiSource,
    /function _stableMutationOptions[\s\S]+headers\.set\('Idempotency-Key', idempotencyKey\)/,
  );
  assert.match(
    apiSource,
    /const stableOptions = _stableMutationOptions\(method, options\);[\s\S]+while \(true\)/,
  );
  assert.match(
    apiSource,
    /async function requestForm[\s\S]+const stableOptions = _stableMutationOptions\(method, options\)/,
  );
  assert.match(
    apiSource,
    /const headers = new Headers\(_requestHeaders\(method, stableOptions\) \|\| \{\}\);/,
  );
});

test('unsupported capabilities fail explicitly instead of returning fake empty data', () => {
  assert.ok(uiCompatSource.includes('capability_not_connected'));
  assert.ok(uiCompatSource.includes('_not_connected(path)'));
  assert.equal(source.includes('尚未接入严格新版数据'), false);
});

test('desktop surfaces fill the available window width', () => {
  assert.match(appSource, /className="min-h-screen bg-\[#F9FAFB\] flex/);
  assert.match(appSource, /className=\{`flex-1 ml-\[60px\].*flex flex-col/s);
  assert.match(styles, /html,\s*body,\s*#root\s*\{\s*min-height:\s*100%/s);
});

test('workspace recovery opens target login and only lists organizations', () => {
  assert.match(appSource, /error instanceof ApiRequestError/);
  assert.match(appSource, /'workspace_secret_missing'/);
  assert.match(
    appSource,
    /cloudApiUrl:\s*targetWorkspace\.cloudApiUrl/,
  );
  assert.match(
    appSource,
    /openCloudAuthModal\('login',\s*\{[\s\S]{0,180}cloudApiUrl:\s*targetWorkspace\.cloudApiUrl,[\s\S]{0,180}organizationName:/,
  );
  assert.match(
    appSource,
    /seed\.clearRememberedAccount\s*\?\s*''\s*:\s*\(currentSessionUser\?\.email/,
  );
  assert.match(
    appSource,
    /\.filter\(\s*\(workspace\) => workspace\.kind === 'organization'/s,
  );
  assert.match(
    appSource,
    /await refreshWorkspaceAwareState\(response, transitionToken\);[\s\S]{0,180}setWorkspaceManagerOpen\(false\);[\s\S]{0,180}已切换工作空间/,
  );
  assert.match(appSource, /'尚未登录组织'/);
  assert.match(appSource, /'没有活动组织工作空间'/);
  assert.equal(appSource.includes('工作空间：未连接组织'), false);
  assert.equal(appSource.includes('`未连接组织 · ${sidebarVisibleClientCount} 客户`'), false);
});

test('workspace bootstrap only loads administrator surfaces for administrators', () => {
  assert.match(
    appSource,
    /name: 'activity-logs'[\s\S]{0,500}primaryRole === 'admin'[\s\S]{0,500}setLogs\(\[\]\)/,
  );
  assert.match(
    appSource,
    /name: nextAuth\.user\?\.primaryRole === 'admin'[\s\S]{0,120}system-admin-settings[\s\S]{0,700}primaryRole === 'admin'[\s\S]{0,700}loadOrgModelBlock/,
  );
  assert.match(
    appSource,
    /正在加载 \$\{nextActiveWorkspace\?\.name[\s\S]{0,1500}nextAuth\.user\?\.primaryRole === 'admin'[\s\S]{0,300}loadSystemAdminSettingsBlock[\s\S]{0,300}loadOrgModelBlock/,
  );
  assert.match(
    appSource,
    /blocked · 仅组织管理员可查看业务操作审计/,
  );
  assert.match(
    appSource,
    /children:\s*canManageOrganization\s*\?[\s\S]{0,160}<TeamSyncPanel/,
  );
  assert.match(
    appSource,
    /currentSessionUser\?\.primaryRole === 'admin'[\s\S]{0,240}<BotMembersPanel/,
  );
  assert.match(
    appSource,
    /身份受限 · 仅组织管理员可查看和触发组织级团队同步/,
  );
});

test('member task pages skip administrator diagnostics and optimistic draft fetches', () => {
  assert.match(
    appSource,
    /if \(currentSessionUser\?\.primaryRole === 'admin'\) \{\s+void resolveDataCenterKernel\(\{\s+scope:\s+\{\s+page: 'task_detail'/,
  );
  assert.match(
    appSource,
    /const canRunAdminDiagnostics = currentSessionUser\?\.primaryRole === 'admin';[\s\S]{0,120}if \(canRunAdminDiagnostics\) \{[\s\S]{0,240}resolveDataCenterKernel/,
  );
  assert.match(
    appSource,
    /\.filter\(\(task\) => \(\s*!isLocalDraftTaskId\(task\.id\)[\s\S]{0,160}task\.scopeMode !== 'PERSONAL_ONLY'/,
  );
  assert.ok(appSource.includes('shouldLoadTaskContextBrief({'));
  assert.match(
    appSource,
    /!isCollapsing && !isLocalDraftTaskId\(taskId\) && !taskSmartBriefs\[taskId\]/,
  );
});

test('task entry points use membership identity and never force a synthetic inbox list', () => {
  assert.match(
    appSource,
    /tasksViewBridgeRef\.current = \{[\s\S]{0,260}viewerAuthorization/,
  );
  assert.match(
    appSource,
    /viewerAuthorization: currentViewerAuthorization[\s\S]{0,6200}const currentTaskMembershipId = currentViewerAuthorization\?\.membershipId \|\| null/,
  );
  for (const dynamicValue of [
    'workspacesState',
    'strictPlanWorkshopState',
    'isOrganizationPlanningLoading',
    'organizationPlanningLoadError',
    'hasRawAuthenticatedSession',
    'recordingSession',
  ]) {
    assert.match(
      appSource,
      new RegExp(`tasksViewBridgeRef\\.current = \\{[\\s\\S]{0,7000}\\b${dynamicValue}\\b`),
    );
  }
  assert.match(
    appSource,
    /const membershipId = currentTaskMembershipId \|\| '';[\s\S]{0,220}id: membershipId/,
  );
  assert.equal(appSource.includes("|| 'list-0'"), false);
  assert.equal(appSource.includes('ownerId: currentSessionUser?.id'), false);
});

test('new task attachments remain pending until the authoritative task exists, then bind to it', () => {
  assert.match(
    appSource,
    /const pendingTaskAttachmentsSnapshot = \[\.\.\.pendingTaskAttachments\]/,
  );
  assert.match(
    appSource,
    /if \(!editingTask\.id\) \{\s*queuePendingTaskAttachments\(fileList\);\s*return;\s*\}[\s\S]{0,180}uploadAttachmentsToTask\(\s*editingTask\.id,\s*fileList/,
  );

  const createIndex = appSource.indexOf(
    'savedTask = await createTask(payload, { sandboxId: saveSandboxId });',
  );
  const bindIndex = appSource.indexOf(
    'await uploadAttachmentsToTask(savedTask.id, pendingTaskAttachmentsSnapshot, {',
  );
  assert.ok(createIndex >= 0, 'new task must be created through the strict task command');
  assert.ok(bindIndex > createIndex, 'pending files may bind only after the task has a stable authority id');
  assert.match(
    appSource,
    /const taskWithUploadedAttachments = await uploadAttachmentsToTask\([\s\S]{0,700}savedTask = taskWithUploadedAttachments;[\s\S]{0,240}upsertLocalTask\(taskWithUploadedAttachments, savedTask\.id/,
  );
  assert.match(
    taskMediaPanelSource,
    /recording\.pending \? '将随任务一并保存' : '播放录音'/,
  );
  assert.equal(taskMediaPanelSource.includes('>待保存</span>'), false);
  assert.equal(appSource.includes('>待保存</span>'), false);
});

test('legacy customer meetings retain people and join inbox, plan, list and calendar surfaces', () => {
  assert.ok(appSource.includes("recordMode: 'customer_meeting'"));
  assert.match(appSource, /collaboratorMembershipIds: selectedTaskCollaborators\.map/);
  assert.match(appSource, /planningCycleId: editingTask\.planLinkSource === 'manager'/);
  assert.ok(appSource.includes('filteredInboundPendingMeetings'));
  assert.ok(appSource.includes('filteredOutboundPendingMeetings'));
  assert.ok(appSource.includes('onToggleMeetingStatus={handleToggleMeetingCompletion}'));
});

test('workbench exposes project knowledge source counts and material boundary', () => {
  assert.ok(appSource.includes('项目知识上下文'));
  assert.ok(appSource.includes('组织共享'));
  assert.ok(appSource.includes('本机私有'));
  assert.ok(appSource.includes('组织云只返回已发布摘要和关系，不下载源文件正文'));
  assert.ok(appSource.includes('本机私有资料不会上传组织云'));
  assert.equal(appSource.includes('整理资料 · 从本机正文生成并发布组织共享摘要'), false);
  assert.match(appSource, /onClick=\{\(\) => void handlePreviewDocumentAutoRepair\(\)\}/);
  assert.ok(appSource.includes('源文件和正文始终留在本机，只向当前组织发布摘要与来源校验信息'));
  assert.ok(uiCompatSource.includes('"knowledgeContext": knowledge_context'));
});

test('task transcript can only be summarized into bounded task details', () => {
  assert.ok(appSource.includes('重新校正'));
  assert.ok(appSource.includes('readOnly'));
  assert.equal(appSource.includes('保存修正版'), false);
  assert.ok(appSource.includes('提炼纪要并写入任务详情'));
  assert.ok(appSource.includes('【录音纪要】'));
  assert.equal(appSource.includes('查看原始转写'), false);
  assert.equal(appSource.includes('原始转写已保留'), false);
  assert.match(appSource, /const descriptionLimit = 20_000/);
  assert.match(appSource, /const minutesHardLimit = Math\.min\(1_800, availableLength\)/);
  assert.equal(appSource.includes('插入任务文字'), false);
  assert.equal(appSource.includes('生成会议纪要'), false);
});

test('workbench explains text correction and clears a stale selection immediately', () => {
  assert.ok(appSource.includes('如需纠错或补充，请选中相关文本'));
  assert.ok(appSource.includes('normalizeAnswerSelectionForMatch'));
  assert.ok(appSource.includes('selectedText.length > 2_000'));
  assert.equal(appSource.includes('selectedText.length > 20_000'), false);
  assert.ok(appSource.includes("document.addEventListener('selectionchange', clearStaleAnswerSelection)"));
  assert.match(
    appSource,
    /selection\.isCollapsed[\s\S]{0,220}selection\.toString\(\)\.trim\(\) !== answerTextSelection\.selectedText[\s\S]{0,120}setAnswerTextSelection\(null\)/,
  );
});

test('empty intelligence surfaces do not inject client-name mocks or example judgments', () => {
  assert.equal(intelligenceSource.includes("selectedWorkObject?.name?.includes('测试机构A')"), false);
  assert.equal(intelligenceSource.includes("targetName.includes('测试机构A')"), false);
  assert.equal(intelligenceSource.includes('_mockOfficialChannelsFor'), false);
  assert.match(
    intelligenceSource,
    /const STAKEHOLDER_PERCEIVABILITY_RESULTS:[^=]+=\s*\{\};/,
  );
  assert.ok(intelligenceSource.includes('not_connected · 尚无严格权威评分'));
  assert.equal(appSource.includes('测试论坛A 面向基金会客户'), false);
  assert.equal(topicsSource.includes('EXAMPLE_JUDGMENT'), false);
  assert.ok(topicsSource.includes('系统不会用示例或宽泛内容制造“已有判断”的假象'));
});

test('cloud sessions immediately consume pending consultation knowledge and retry every minute', () => {
  assert.ok(appSource.includes('processPendingConsultationKnowledgeRequests'));
  assert.ok(appSource.includes('consultationKnowledgeSyncInFlightRef'));
  assert.match(
    appSource,
    /if \(!hasAuthenticatedSession \|\| !isCloudSession\) return undefined;/,
  );
  assert.match(
    appSource,
    /const summary = await processPendingConsultationKnowledgeRequests\(\);/,
  );
  assert.match(
    appSource,
    /void run\(\);[\s\S]{0,120}window\.setInterval\(\(\) => \{[\s\S]{0,80}void run\(\);[\s\S]{0,80}60_000/,
  );
  assert.ok(appSource.includes('[consultation-knowledge] pending sync failed'));
  assert.ok(appSource.includes('[consultation-knowledge] pending sync completed with failures'));
  assert.match(
    appSource,
    /summary\.items\.some\(\(item\) => item\.clientId === targetClientId\)[\s\S]{0,120}await refreshWorkspace\(targetClientId\);/,
  );
});

test('empty workbench only offers real project creation, never fake demo data', () => {
  assert.equal(appSource.includes('载入演示数据'), false);
  assert.equal(appSource.includes('loadDemoData'), false);
  assert.ok(appSource.includes('先创建一个真实项目'));
});

test('link import does not promise unavailable browser-cookie access', () => {
  assert.equal(appSource.includes('使用浏览器登录态读取链接'), false);
  assert.ok(appSource.includes('当前严格版不会读取浏览器 Cookie'));
});

test('team status panel reflects strict outbox evidence instead of a legacy file queue', () => {
  assert.ok(teamSyncSource.includes('stats?.statusCounts'));
  assert.ok(teamSyncSource.includes('不代表源文件上传队列'));
  assert.ok(teamSyncSource.includes('成员源文件正文只保留在各自本机'));
  assert.equal(teamSyncSource.includes('team_documents'), false);
  assert.equal(teamSyncSource.includes('source_registry'), false);
  assert.equal(teamSyncSource.includes('扫描新文件入队'), false);
  assert.equal(teamSyncSource.includes('立即同步'), false);
});

test('logout immediately hides organization meetings and Yiyu-only maintenance controls', () => {
  assert.match(
    appSource,
    /async function handleLogoutFromUi\(\)[\s\S]{0,900}resetBusinessWorkspaceTransientState\(\)[\s\S]{0,240}setAuthState\(response\)/,
  );
  assert.match(
    appSource,
    /const resetBusinessWorkspaceTransientState = \(\) => \{[\s\S]{0,2200}setCustomerMeetings\(\[\]\)/,
  );
  assert.match(
    appSource,
    /if \(!hasAuthenticatedSession \|\| activeTab !== 'tasks'\) \{\s*setCustomerMeetings\(\[\]\)/,
  );
  assert.match(
    appSource,
    /const canShowMaintenanceSyncPanel = Boolean\(\s*canUseCollabSync\s*&& hasAuthenticatedSession\s*&& isCloudSession\s*&& currentMembershipStatus === 'approved'/,
  );
  assert.equal(appSource.includes('void logout().then'), false);
});

test('logged-out settings hide organization snapshots and never label a fallback model as connected', () => {
  assert.match(
    appSource,
    /if \(!hasAuthenticatedSession\) \{[\s\S]{0,2600}组织名称、云地址、组织模型与集成配置已隐藏/,
  );
  assert.match(
    systemStatusSource,
    /if \(!canSyncOrganizationAi\) \{[\s\S]{0,260}value: '未接通'/,
  );
  assert.equal(systemStatusSource.includes('const shortName = shortAiModelLabel(\n        health.ai.provider'), false);
});
