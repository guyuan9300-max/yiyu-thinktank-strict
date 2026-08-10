import appLogoUrl from '../../assets/brand/app-logo-ai.png';
import brandAvatarUrl from '../../assets/brand/brand-avatar-yiyu.png';

type BrandLogoMarkProps = {
  className?: string;
};

export function BrandLogoMark({ className = 'w-8 h-8' }: BrandLogoMarkProps) {
  return (
    <div className={`${className} flex shrink-0 items-center justify-center overflow-hidden transition-transform duration-300 hover:scale-105`}>
      <img
        src={brandAvatarUrl}
        alt="益语智库"
        className="h-full w-full object-contain"
        draggable={false}
      />
    </div>
  );
}

export function AppLogoMark({ className = 'w-8 h-8' }: BrandLogoMarkProps) {
  return (
    <div className={`${className} flex shrink-0 items-center justify-center overflow-hidden rounded-[22%] transition-transform duration-300 hover:scale-105`}>
      <img
        src={appLogoUrl}
        alt="AI"
        className="h-full w-full object-cover"
        draggable={false}
      />
    </div>
  );
}
