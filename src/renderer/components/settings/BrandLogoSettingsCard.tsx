import appLogoUrl from '../../assets/brand/app-logo-ai.png';
import brandAvatarUrl from '../../assets/brand/brand-avatar-yiyu.png';
import {
  organizationBrandDisplayName,
  useOrganizationBrand,
} from '../../lib/organizationBrandStore';

type BrandLogoMarkProps = {
  className?: string;
  organizationScopeKey?: string | null;
};

export function BrandLogoMark({
  className = 'w-8 h-8',
  organizationScopeKey,
}: BrandLogoMarkProps) {
  const brand = useOrganizationBrand(organizationScopeKey);
  const displayName = organizationBrandDisplayName(brand);
  return (
    <div className={`${className} flex shrink-0 items-center justify-center overflow-hidden transition-transform duration-300 hover:scale-105`}>
      <img
        src={brand.logoDataUrl || brandAvatarUrl}
        alt={`${displayName} Logo`}
        className="h-full w-full object-contain"
        draggable={false}
      />
    </div>
  );
}

export function BrandDisplayName({
  organizationScopeKey,
  className,
}: {
  organizationScopeKey?: string | null;
  className?: string;
}) {
  const brand = useOrganizationBrand(organizationScopeKey);
  return <span className={className}>{organizationBrandDisplayName(brand)}</span>;
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
