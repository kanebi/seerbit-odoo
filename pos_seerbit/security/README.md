# Security Configuration for Seerbit Module

## Overview
This directory contains security configuration files for the Seerbit Odoo module.

## Files

### `ir.model.access.csv`
This file defines access rights for Seerbit-related models.

#### Format
```
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
```

#### Permissions
- `perm_read`: Can read records (1=Yes, 0=No)
- `perm_write`: Can modify records (1=Yes, 0=No)
- `perm_create`: Can create new records (1=Yes, 0=No)
- `perm_unlink`: Can delete records (1=Yes, 0=No)

#### Access Rules

1. **POS Payment Methods** (`pos.payment.method`)
   - **Group**: `point_of_sale.group_pos_user` (POS Users)
   - **Permissions**: Read, Write, Create (but not Delete)
   - **Purpose**: Allow POS users to manage Seerbit payment methods

2. **Configuration Settings** (`res.config.settings`)
   - **Group**: `base.group_system` (System Administrators)
   - **Permissions**: Read, Write, Create (but not Delete)
   - **Purpose**: Allow system administrators to configure Seerbit settings

## Security Model

### User Groups
- **POS Users**: Can manage payment methods but cannot delete them
- **System Administrators**: Can configure module settings

### Data Protection
- Payment methods cannot be deleted by regular users (prevents accidental data loss)
- Configuration changes require system administrator privileges
- All operations are logged for audit purposes

## Best Practices
1. Always use the principle of least privilege
2. Log all security-sensitive operations
3. Provide clear error messages for access denied scenarios
4. Test access rights thoroughly during development 