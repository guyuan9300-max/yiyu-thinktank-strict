type MaterialBoundary = {
  sourceFileContentIncluded?: boolean;
  sourceFilePathsIncluded?: boolean;
  storageLocatorsIncluded?: boolean;
  unpublishedDocumentContentIncluded?: boolean;
  localPrivateSource?: boolean;
  localPrivateUploadedToOrganizationCloud?: boolean;
  localSourcePathsIncludedInContext?: boolean;
};

const INCLUDED_LABELS: Array<[keyof MaterialBoundary, string, string]> = [
  ['sourceFileContentIncluded', '包含源文件正文', '未包含源文件正文'],
  ['sourceFilePathsIncluded', '包含源文件路径', '未包含源文件路径'],
  ['storageLocatorsIncluded', '包含存储定位信息', '未包含存储定位信息'],
  ['unpublishedDocumentContentIncluded', '包含未发布文档正文', '未包含未发布文档正文'],
  ['localPrivateSource', '使用了本机私有源资料', '未使用本机私有源资料'],
  ['localSourcePathsIncludedInContext', '包含本机源文件路径', '未包含本机源文件路径'],
];

export function formatTaskContextMaterialBoundary(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '';

  const boundary = value as MaterialBoundary;
  const notes = INCLUDED_LABELS.flatMap(([key, included, excluded]) => {
    if (boundary[key] === true) return [included];
    if (boundary[key] === false) return [excluded];
    return [];
  });
  if (boundary.localPrivateUploadedToOrganizationCloud === true) {
    notes.push('本机私有资料已上传组织云');
  } else if (boundary.localPrivateUploadedToOrganizationCloud === false) {
    notes.push('本机私有资料未上传组织云');
  }
  return notes.length > 0 ? `资料边界：${notes.join('；')}。` : '';
}
