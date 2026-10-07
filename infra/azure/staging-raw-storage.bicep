targetScope = 'resourceGroup'

@description('Existing Container Apps environment in this resource group.')
param environmentName string = 'cae-prms-staging'

param location string = resourceGroup().location

@description('Deterministic storage account name; override to use another new account.')
param storageAccountName string = 'stprms${uniqueString(resourceGroup().id)}'

param shareName string = 'prms-raw'
param environmentStorageName string = 'prms-raw'

resource account 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}

resource files 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: account
  name: 'default'
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: files
  name: shareName
  properties: {
    enabledProtocols: 'SMB'
    shareQuota: 100
    accessTier: 'TransactionOptimized'
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}

resource mount 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: environment
  name: environmentStorageName
  properties: {
    azureFile: {
      accountName: account.name
      accountKey: account.listKeys().keys[0].value
      shareName: share.name
      accessMode: 'ReadWrite'
    }
  }
}

// Credentials stay in the resource provider; no key is returned or written to Git.
output storageAccount string = account.name
output fileShare string = share.name
output environmentStorage string = mount.name
