# Creates a root personal access token for Terraform and the portal.
# Prints the plaintext token once. Re-running revokes the previous token of the same name.

name = "dev-portal-terraform"
user = User.find_by_username("root")
abort("root user not found") unless user

PersonalAccessToken.active.where(user_id: user.id, name: name).find_each(&:revoke!)

pat = user.personal_access_tokens.create!(
  name: name,
  scopes: %w[api read_repository write_repository],
  expires_at: 90.days.from_now
)

abort("token was not returned") if pat.token.to_s.empty?
puts "TOKEN=#{pat.token}"
