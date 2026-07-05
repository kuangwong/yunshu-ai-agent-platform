import assert from 'node:assert/strict';
import {
  isDirectRenderableUrl,
  resolveFsPreviewUrl,
} from '../src/utils/workspaceFilePreview.ts';

const blobUrl = 'blob:http://localhost:5173/abc-123';

assert.equal(isDirectRenderableUrl(blobUrl), true);
assert.equal(resolveFsPreviewUrl(blobUrl), blobUrl);
assert.equal(
  resolveFsPreviewUrl('/data/agent_workspaces/user__1/uploads/photo.jpg'),
  '/api/v1/chat/fs/preview?path=%2Fdata%2Fagent_workspaces%2Fuser__1%2Fuploads%2Fphoto.jpg',
);

console.log('workspaceFilePreview.test.ts passed');
