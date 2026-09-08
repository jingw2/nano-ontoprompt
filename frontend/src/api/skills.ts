import { apiClientV2 } from './client'

export interface SkillPackage {
  id: string
  name: string
  status: string
}

export interface SkillVersion {
  id: string
  package_id: string
  version_no: number
  approval_status: 'pending' | 'approved' | 'rejected'
  canonical_hash: string
  manifest: Record<string, unknown>
}

export interface SkillSignatureInput {
  public_key_hex: string
  signature_hex: string
  signer_identity?: string
}

export const skillsApi = {
  listPackages: () => apiClientV2.get<{ items: SkillPackage[] }>('/skills/packages'),
  createPackage: (name: string) => apiClientV2.post<SkillPackage>('/skills/packages', { name }),
  listVersions: (packageId?: string) =>
    apiClientV2.get<{ items: SkillVersion[] }>(
      `/skills/versions${packageId ? `?package_id=${encodeURIComponent(packageId)}` : ''}`,
    ),
  createVersion: (packageId: string, manifest: Record<string, unknown>, signatures: SkillSignatureInput[]) =>
    apiClientV2.post<SkillVersion>('/skills/versions', { package_id: packageId, manifest, signatures }),
  approveVersion: (versionId: string) =>
    apiClientV2.post<{ id: string; approval_status: string }>(`/skills/versions/${versionId}/approve`),
}
