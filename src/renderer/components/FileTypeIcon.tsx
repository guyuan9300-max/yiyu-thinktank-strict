import wordIcon from '../assets/file-icons/word.png';
import excelIcon from '../assets/file-icons/excel.png';
import pptIcon from '../assets/file-icons/ppt.png';
import pdfIcon from '../assets/file-icons/pdf.png';
import mdIcon from '../assets/file-icons/md.png';
import txtIcon from '../assets/file-icons/txt.png';
import imageIcon from '../assets/file-icons/image.png';
import aiIcon from '../assets/file-icons/ai.png';

const EXTENSION_ICON_MAP: Record<string, string> = {
  doc: wordIcon,
  docx: wordIcon,
  xls: excelIcon,
  xlsx: excelIcon,
  csv: excelIcon,
  ppt: pptIcon,
  pptx: pptIcon,
  pdf: pdfIcon,
  md: mdIcon,
  markdown: mdIcon,
  txt: txtIcon,
  png: imageIcon,
  jpg: imageIcon,
  jpeg: imageIcon,
  webp: imageIcon,
  gif: imageIcon,
  bmp: imageIcon,
  svg: imageIcon,
};

function resolveIconSource(path: string | null | undefined): string {
  if (!path) return aiIcon;
  const lastDot = path.lastIndexOf('.');
  if (lastDot < 0 || lastDot === path.length - 1) return aiIcon;
  const ext = path.slice(lastDot + 1).toLowerCase();
  return EXTENSION_ICON_MAP[ext] || aiIcon;
}

interface FileTypeIconProps {
  path?: string | null;
  size?: number;
  className?: string;
}

export function FileTypeIcon({ path, size = 32, className = '' }: FileTypeIconProps) {
  const src = resolveIconSource(path);
  return (
    <span
      className={`inline-flex items-center justify-center bg-white rounded-md border border-gray-100 shadow-[0_1px_2px_rgba(0,0,0,0.04)] overflow-hidden shrink-0 ${className}`}
      style={{ width: size, height: size }}
    >
      <img
        src={src}
        alt=""
        draggable={false}
        style={{ width: '78%', height: '78%', objectFit: 'contain' }}
      />
    </span>
  );
}

export function hasOpenableFile(path: string | null | undefined): boolean {
  if (!path) return false;
  const lastDot = path.lastIndexOf('.');
  if (lastDot < 0 || lastDot === path.length - 1) return false;
  return true;
}
