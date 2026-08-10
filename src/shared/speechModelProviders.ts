/**
 * 语音识别 provider 描述符。
 *
 * 决定系统设置 → 语音识别模型 section 的 dropdown 选项 + 每个 provider 暴露哪些字段。
 *
 * 新增 provider 时只需：
 * 1. 在 `SPEECH_MODEL_PROVIDERS` 里加一项
 * 2. 后端 `services/speech_recognition/registry.py` 注册具体实现
 *
 * **注意：** 后端只用 `provider` 名做路由，字段语义完全由前端这份描述符决定。
 * 后端 `credentials_json` / `extra_config_json` 是无模式的 JSON，灵活承载任何字段。
 */

export interface SpeechProviderField {
  key: string;
  label: string;
  type: 'text' | 'password' | 'textarea';
  placeholder?: string;
  helper?: string;
}

export interface SpeechProviderModelOption {
  id: string;
  label: string;
  description?: string;
}

export interface SpeechProviderExtraSelect {
  key: string;
  label: string;
  type: 'select';
  options: Array<{ value: string; label: string }>;
  defaultValue: string;
  helper?: string;
}

export interface SpeechProviderDescriptor {
  id: string;
  label: string;
  /** 该 provider 是否 I1a 阶段就支持"测试连接"+"实际转写"。占位 provider = false。 */
  supported: boolean;
  /** UI 上的副标题说明。 */
  description: string;
  /** 鉴权字段（敏感字段用 password type 做 mask）。 */
  credentialFields: SpeechProviderField[];
  /** 该 provider 下可选的具体模型 ID。 */
  models: SpeechProviderModelOption[];
  /** 额外配置项（语种、region、endpoint 覆盖等）。 */
  extraFields: SpeechProviderExtraSelect[];
  /** 如果 supported=false，UI 上显示此提示并禁用测试连接。 */
  unsupportedHint?: string;
}

export const SPEECH_MODEL_PROVIDERS: SpeechProviderDescriptor[] = [
  {
    id: 'local_sensevoice',
    label: '本地 · SenseVoice（达摩院 · 推荐 ⭐）',
    supported: true,
    description:
      '阿里达摩院 SenseVoice-Small（约 240MB 模型，按需下载）。完全本地推理，音频不出本机，无需对象存储。' +
      '中文场景准确率优于火山豆包标准版；M 系列 Mac 上 1 小时录音约 20-40 秒转完。',
    credentialFields: [],
    models: [
      { id: 'sense-voice-small', label: 'SenseVoice-Small (int8 量化版)', description: '约 240MB · sherpa-onnx 后端 · 支持中英日韩粤五语' },
    ],
    extraFields: [],
  },
  {
    id: 'volcano',
    label: '火山引擎（豆包大模型录音文件识别）',
    supported: true,
    description: '火山引擎豆包大模型录音文件识别。中文场景准确率高，最长支持 5 小时音频/512MB 文件。endpoint 走 openspeech.bytedance.com 旧版控制台 API（不是 ark）。',
    credentialFields: [
      { key: 'app_id', label: 'App ID', type: 'text', placeholder: '火山控制台 → 语音技术 → 你的应用 App ID（数字串）' },
      { key: 'access_token', label: 'Access Token', type: 'password', placeholder: '火山控制台 → 语音技术 → API Access Token' },
    ],
    models: [
      { id: 'bigmodel', label: '豆包·录音文件识别（model_name=bigmodel）', description: '当前火山只支持这一个 model_name' },
    ],
    extraFields: [
      {
        key: 'resource_id',
        label: '模型资源 ID',
        type: 'select',
        options: [
          { value: 'volc.bigasr.auc', label: '豆包录音文件识别 1.0（volc.bigasr.auc）' },
          { value: 'volc.seedasr.auc', label: '豆包录音文件识别 2.0 · Seed-ASR（volc.seedasr.auc）' },
        ],
        defaultValue: 'volc.bigasr.auc',
        helper: '对应火山控制台的"模型版本"。先选 1.0；2.0 是 Seed-ASR，额外支持图片上下文。',
      },
      {
        key: 'language',
        label: '识别语种',
        type: 'select',
        options: [
          { value: '', label: '自动（中英文 + 部分方言）' },
          { value: 'zh-CN', label: '中文普通话（zh-CN）' },
          { value: 'en-US', label: '英文（en-US）' },
          { value: 'yue-CN', label: '粤语（yue-CN）' },
          { value: 'ja-JP', label: '日语（ja-JP）' },
          { value: 'ko-KR', label: '韩语（ko-KR）' },
        ],
        defaultValue: '',
      },
    ],
  },
  {
    id: 'openai_whisper',
    label: 'OpenAI Whisper',
    supported: false,
    description: 'OpenAI Whisper API。海外网络可达且能接受 OpenAI 数据合规条款的场景适用。',
    credentialFields: [
      { key: 'api_key', label: 'API Key', type: 'password', placeholder: 'sk-...' },
      { key: 'base_url', label: 'API Base URL（可选）', type: 'text', placeholder: 'https://api.openai.com/v1' },
    ],
    models: [{ id: 'whisper-1', label: 'whisper-1' }],
    extraFields: [
      {
        key: 'language',
        label: '识别语种',
        type: 'select',
        options: [
          { value: 'zh', label: '中文' },
          { value: 'en', label: '英文' },
          { value: 'auto', label: '自动识别' },
        ],
        defaultValue: 'auto',
      },
    ],
    unsupportedHint: '暂未实现 OpenAI Whisper 的"测试连接"和实际转写，敬请期待。',
  },
  {
    id: 'aliyun_tongyi',
    label: '阿里通义听悟',
    supported: false,
    description: '阿里云通义听悟语音识别。',
    credentialFields: [
      { key: 'access_key_id', label: 'AccessKey ID', type: 'text' },
      { key: 'access_key_secret', label: 'AccessKey Secret', type: 'password' },
      { key: 'app_key', label: 'App Key', type: 'text' },
    ],
    models: [{ id: 'paraformer-v1', label: 'paraformer-v1' }],
    extraFields: [],
    unsupportedHint: '暂未实现阿里通义听悟接入，敬请期待。',
  },
  {
    id: 'xunfei',
    label: '科大讯飞',
    supported: false,
    description: '科大讯飞语音识别（开放平台）。',
    credentialFields: [
      { key: 'app_id', label: 'APP ID', type: 'text' },
      { key: 'api_key', label: 'API Key', type: 'password' },
      { key: 'api_secret', label: 'API Secret', type: 'password' },
    ],
    models: [{ id: 'iat', label: 'IAT 实时语音转写' }],
    extraFields: [],
    unsupportedHint: '暂未实现讯飞接入，敬请期待。',
  },
];

export function findSpeechProvider(id: string): SpeechProviderDescriptor | undefined {
  return SPEECH_MODEL_PROVIDERS.find((p) => p.id === id);
}
