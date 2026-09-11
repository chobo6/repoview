package com.example;

public interface UserRepository {
    List<User> findAll();

    List<Order> findOrdersByUserId(Long userId);
}
