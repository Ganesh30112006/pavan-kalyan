import type { ApplicationType, Exposure, RuntimeType } from '../types/api';

export const applicationTypeLabel: Record<ApplicationType, string> = {
  web_service: 'Web service',
  api: 'API',
  worker: 'Background worker',
};

export const applicationTypeDescription: Record<ApplicationType, string> = {
  web_service: 'Serves a website or web application over HTTP.',
  api: 'Serves an HTTP API to other services or clients.',
  worker: 'Processes jobs or messages without serving public HTTP traffic.',
};

export const runtimeLabel: Record<RuntimeType, string> = {
  node: 'Node.js',
  python: 'Python',
  java: 'Java',
  go: 'Go',
  dotnet: '.NET',
  ruby: 'Ruby',
  php: 'PHP',
  other: 'Other',
};

export const exposureLabel: Record<Exposure, string> = {
  public: 'Public',
  private: 'Private',
};
