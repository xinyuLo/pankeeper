import { USE_MOCK } from './http';
import { listDdItems } from './modules/dd';
import { listAccounts } from './modules/accounts';
import { listPaTasks } from './modules/tasks';
export function hydrateAll() {
    if (USE_MOCK)
        return;
    listDdItems().catch(() => { });
    listAccounts().catch(() => { });
    listPaTasks().catch(() => { });
}
