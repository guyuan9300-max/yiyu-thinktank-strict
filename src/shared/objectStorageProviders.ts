/**
 * 对象存储 provider 描述符。
 *
 * 决定系统设置 → 对象存储 section 的 dropdown 选项 + 每个 provider 暴露哪些字段。
 *
 * 新增 provider 时只需：
 * 1. 在 `OBJECT_STORAGE_PROVIDERS` 里加一项
 * 2. 后端 `services/object_storage/registry.py` 注册具体实现
 */

export interface ObjectStorageField {
  key: string;
  label: string;
  type: 'text' | 'password';
  placeholder?: string;
  helper?: string;
}

export interface ObjectStorageDescriptor {
  id: string;
  label: string;
  /** 该 provider 是否当前已实现"测试连接"+"上传"。占位 provider = false。 */
  supported: boolean;
  /** UI 上的副标题。 */
  description: string;
  /** 鉴权字段（敏感字段用 password type）。 */
  credentialFields: ObjectStorageField[];
  /** 额外配置：endpoint / region / bucket 等。 */
  extraFields: ObjectStorageField[];
  /** 如果 supported=false，UI 上显示此提示并禁用测试连接。 */
  unsupportedHint?: string;
}

export const OBJECT_STORAGE_PROVIDERS: ObjectStorageDescriptor[] = [
  {
    id: 'volcano_tos',
    label: '火山引擎 TOS（对象存储）',
    supported: true,
    description:
      '火山引擎 TOS。和你已接入的火山豆包大模型同一账号，凭证在火山控制台 → 访问控制 → 访问密钥拿。' +
      '需要先在 TOS 控制台创建一个桶（推荐区域：cn-beijing）。',
    credentialFields: [
      {
        key: 'access_key_id',
        label: 'Access Key ID',
        type: 'text',
        placeholder: '火山引擎控制台 → 访问控制 → API 访问密钥',
      },
      {
        key: 'secret_access_key',
        label: 'Secret Access Key',
        type: 'password',
        placeholder: '与 Access Key ID 配对的密钥',
      },
    ],
    extraFields: [
      {
        key: 'endpoint',
        label: 'Endpoint',
        type: 'text',
        placeholder: 'tos-cn-beijing.volces.com',
        helper: '默认 tos-cn-beijing.volces.com；如桶在其他区域请按控制台显示填。',
      },
      {
        key: 'region',
        label: 'Region',
        type: 'text',
        placeholder: 'cn-beijing',
        helper: '与 Endpoint 对应的区域代码。',
      },
      {
        key: 'bucket',
        label: 'Bucket（桶名）',
        type: 'text',
        placeholder: '你在 TOS 控制台创建的桶名',
        helper: '建议为工作台文件中转单独建一个私有桶，命名如 yiyu-workbench-files-prod。',
      },
    ],
  },
  {
    id: 'aliyun_oss',
    label: '阿里云 OSS',
    supported: false,
    description: '阿里云对象存储 OSS。',
    credentialFields: [
      { key: 'access_key_id', label: 'AccessKey ID', type: 'text' },
      { key: 'secret_access_key', label: 'AccessKey Secret', type: 'password' },
    ],
    extraFields: [
      { key: 'endpoint', label: 'Endpoint', type: 'text', placeholder: 'oss-cn-hangzhou.aliyuncs.com' },
      { key: 'bucket', label: 'Bucket', type: 'text' },
    ],
    unsupportedHint: '暂未实现阿里云 OSS 接入，敬请期待。',
  },
  {
    id: 'aws_s3',
    label: 'AWS S3',
    supported: false,
    description: 'AWS S3（也可用于 S3 兼容的第三方对象存储）。',
    credentialFields: [
      { key: 'access_key_id', label: 'Access Key ID', type: 'text' },
      { key: 'secret_access_key', label: 'Secret Access Key', type: 'password' },
    ],
    extraFields: [
      { key: 'endpoint', label: 'Endpoint（可选）', type: 'text' },
      { key: 'region', label: 'Region', type: 'text', placeholder: 'us-east-1' },
      { key: 'bucket', label: 'Bucket', type: 'text' },
    ],
    unsupportedHint: '暂未实现 AWS S3 接入，敬请期待。',
  },
];

export function findObjectStorageProvider(id: string): ObjectStorageDescriptor | undefined {
  return OBJECT_STORAGE_PROVIDERS.find((p) => p.id === id);
}
