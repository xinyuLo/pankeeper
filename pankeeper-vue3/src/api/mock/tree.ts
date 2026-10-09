import type { TreeNode } from '@/types/model';
export const SHARE_TREE: TreeNode[] = [
    {
        name: '庆余年.第二季.4K.HDR.国粤双语',
        kids: [
            {
                name: '第 01-10 集',
                kids: [
                    { name: '庆余年.S02E01.mkv', size: '3.1 GB' },
                    { name: '庆余年.S02E02.mkv', size: '2.9 GB' },
                    { name: '… 共 10 项', size: '31 GB' },
                ],
            },
            { name: '第 11-20 集', kids: [{ name: '… 共 10 项', size: '30 GB' }] },
            { name: '第 21-30 集', kids: [{ name: '… 共 10 项', size: '30 GB' }] },
            { name: '第 31-36 集', kids: [{ name: '… 共 6 项', size: '18 GB' }] },
            {
                name: '字幕',
                kids: [
                    { name: '简体.srt', size: '120 KB' },
                    { name: '繁体.srt', size: '118 KB' },
                ],
            },
        ],
    },
];
export const MINE_TREE: TreeNode[] = [
    {
        name: '我的资源',
        path: '/',
        kids: [
            {
                name: '影视',
                path: '/我的资源/影视',
                kids: [
                    {
                        name: '电视剧',
                        path: '/我的资源/影视/电视剧',
                        kids: [
                            {
                                name: '国产剧',
                                path: '/我的资源/影视/电视剧/国产剧',
                                kids: [
                                    {
                                        name: '庆余年2',
                                        path: '/我的资源/影视/电视剧/国产剧/庆余年2',
                                        kids: [{ name: '（空）', path: '/我的资源/影视/电视剧/国产剧/庆余年2', size: '0 项' }],
                                    },
                                ],
                            },
                            { name: '欧美剧', path: '/我的资源/影视/电视剧/欧美剧', kids: [] },
                            { name: '日韩剧', path: '/我的资源/影视/电视剧/日韩剧', kids: [] },
                        ],
                    },
                    {
                        name: '电影',
                        path: '/我的资源/影视/电影',
                        kids: [{ name: '国产科幻', path: '/我的资源/影视/电影/国产科幻', kids: [] }],
                    },
                    { name: '纪录片', path: '/我的资源/影视/纪录片', kids: [] },
                ],
            },
            { name: '备份', path: '/我的资源/备份', kids: [] },
            { name: '软件', path: '/我的资源/软件', kids: [] },
        ],
    },
];
export const DD_TREES: Record<string, TreeNode[]> = {
    baidu: [
        { name: '影视', kids: [{ name: '国产剧' }, { name: '电影' }, { name: '纪录片' }, { name: '综艺' }] },
        { name: '网盘备份', kids: [{ name: '照片' }, { name: '文档' }] },
        { name: '软件' },
    ],
    quark: [{ name: '媒体', kids: [{ name: '剧集' }, { name: '动漫' }, { name: '电影' }] }, { name: '临时' }],
    '115': [{ name: '影视', kids: [{ name: '电影' }, { name: '剧集' }] }, { name: '音乐' }],
};
