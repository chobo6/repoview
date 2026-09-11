package com.example;

public class UserService {
    private final UserRepository repository;

    public UserService(UserRepository repository) {
        this.repository = repository;
    }

    public List<User> findAllWithOrders() {
        List<User> users = repository.findAll();
        for (User user : users) {
            user.setOrders(repository.findOrdersByUserId(user.getId()));
        }
        return users;
    }
}
