-- Demo users. In production this table is fed by the org's identity provider;
-- permission maps to the diagram's view/comment split.

INSERT INTO users (id, name, emoji, permission) VALUES
    ('dana', 'Dana (Designer)', '🎨', 'comment'),
    ('evan', 'Evan (Engineer)', '🛠️', 'comment'),
    ('vic',  'Vic (Viewer)',    '👀', 'view');
